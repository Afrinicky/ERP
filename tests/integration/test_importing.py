"""Validated import. PRD 9.6 requires a dry run, error reporting and clean rollback."""

from __future__ import annotations

import csv
import io
from typing import ClassVar

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from nickland.application.context import Actor
from nickland.application.services.importing import ImportKind, ImportService, RowOutcome
from nickland.application.services.parties import PartyService
from nickland.domain.errors import PermissionDeniedError, ValidationError
from nickland.persistence.catalogue_models import Party, Product
from nickland.platform.clock import FixedClock
from tests.conftest import make_actor


@pytest.fixture
def importer(db: Session, clock: FixedClock) -> ImportService:
    return ImportService(db, clock=clock)


def csv_bytes(columns: list[str], rows: list[list[str]]) -> bytes:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(columns)
    writer.writerows(rows)
    return buffer.getvalue().encode("utf-8")


PRODUCT_COLUMNS = ["sku", "name", "stock_unit", "item_class", "manufacturer", "list_price"]


def product_rows(count: int, *, start: int = 1) -> list[list[str]]:
    return [
        [f"SKU-{index:05d}", f"Product {index}", "ea", "stocked", "Medisafe", "120.50"]
        for index in range(start, start + count)
    ]


class TestTemplate:
    @pytest.mark.parametrize("kind", [ImportKind.PRODUCTS, ImportKind.PARTIES])
    def test_a_template_documents_each_column(
        self, importer: ImportService, kind: ImportKind
    ) -> None:
        rows = list(csv.reader(io.StringIO(importer.template(kind))))
        assert len(rows) == 2
        assert len(rows[0]) == len(rows[1])
        assert "sku" in rows[0] or "code" in rows[0]


class TestPreview:
    def test_a_clean_file_previews_without_writing(
        self, importer: ImportService, admin: Actor, db: Session
    ) -> None:
        content = csv_bytes(PRODUCT_COLUMNS, product_rows(5))
        report = importer.preview(
            admin, kind=ImportKind.PRODUCTS, filename="catalogue.csv", content=content
        )
        assert report.total == 5
        assert report.to_create == 5
        assert report.rejected == 0
        assert report.acceptable
        assert not report.committed
        assert db.scalar(select(func.count()).select_from(Product)) == 0

    def test_every_fault_is_reported_not_just_the_first(
        self, importer: ImportService, admin: Actor
    ) -> None:
        """A user correcting a file wants the whole list, not one fault at a time."""
        content = csv_bytes(
            PRODUCT_COLUMNS,
            [
                ["", "No SKU", "ea", "stocked", "", ""],
                ["SKU-2", "", "ea", "stocked", "", ""],
                ["SKU-3", "Fine", "nonexistent", "stocked", "", ""],
                ["SKU-4", "Bad price", "ea", "stocked", "", "not-a-number"],
                ["SKU-5", "Bad class", "ea", "widget", "", ""],
            ],
        )
        report = importer.preview(
            admin, kind=ImportKind.PRODUCTS, filename="bad.csv", content=content
        )
        assert report.rejected == 5
        assert not report.acceptable
        assert {row.field for row in report.errors} >= {
            "sku",
            "name",
            "stock_unit",
            "list_price",
            "item_class",
        }

    def test_each_fault_names_its_line_and_remedy(
        self, importer: ImportService, admin: Actor
    ) -> None:
        content = csv_bytes(
            PRODUCT_COLUMNS,
            [
                ["SKU-1", "Fine", "ea", "stocked", "", "1.00"],
                ["SKU-2", "Bad", "ea", "stocked", "", "twelve"],
            ],
        )
        report = importer.preview(
            admin, kind=ImportKind.PRODUCTS, filename="bad.csv", content=content
        )
        fault = report.errors[0]
        assert fault.line == 3
        assert fault.reference == "SKU-2"
        assert "decimal" in fault.message

    def test_a_second_import_of_the_same_file_amends(
        self, importer: ImportService, admin: Actor
    ) -> None:
        content = csv_bytes(PRODUCT_COLUMNS, product_rows(3))
        importer.commit(admin, kind=ImportKind.PRODUCTS, filename="catalogue.csv", content=content)
        report = importer.preview(
            admin, kind=ImportKind.PRODUCTS, filename="catalogue.csv", content=content
        )
        assert report.to_amend == 3
        assert report.to_create == 0

    def test_an_empty_file_is_reported_as_such(self, importer: ImportService, admin: Actor) -> None:
        report = importer.preview(
            admin,
            kind=ImportKind.PRODUCTS,
            filename="empty.csv",
            content=csv_bytes(PRODUCT_COLUMNS, []),
        )
        assert report.total == 0
        assert not report.acceptable
        assert "no data rows" in report.summary()

    def test_trailing_empty_rows_are_ignored(self, importer: ImportService, admin: Actor) -> None:
        content = csv_bytes(
            PRODUCT_COLUMNS,
            [*product_rows(2), ["", "", "", "", "", ""], ["", "", "", "", "", ""]],
        )
        report = importer.preview(
            admin, kind=ImportKind.PRODUCTS, filename="catalogue.csv", content=content
        )
        assert report.total == 2


class TestCommit:
    def test_a_clean_file_is_written(
        self, importer: ImportService, admin: Actor, db: Session
    ) -> None:
        content = csv_bytes(PRODUCT_COLUMNS, product_rows(10))
        report = importer.commit(
            admin, kind=ImportKind.PRODUCTS, filename="catalogue.csv", content=content
        )
        assert report.committed
        assert db.scalar(select(func.count()).select_from(Product)) == 10

    def test_one_bad_row_refuses_the_whole_file(
        self, importer: ImportService, admin: Actor, db: Session
    ) -> None:
        """An import is all or nothing: a partially loaded catalogue cannot result."""
        content = csv_bytes(
            PRODUCT_COLUMNS,
            [*product_rows(99), ["SKU-BAD", "Bad", "nonexistent", "stocked", "", ""]],
        )
        report = importer.commit(
            admin, kind=ImportKind.PRODUCTS, filename="catalogue.csv", content=content
        )
        assert not report.committed
        assert report.rejected == 1
        assert db.scalar(select(func.count()).select_from(Product)) == 0

    def test_the_run_is_recorded_whether_it_commits_or_not(
        self, importer: ImportService, admin: Actor
    ) -> None:
        good = csv_bytes(PRODUCT_COLUMNS, product_rows(2))
        bad = csv_bytes(PRODUCT_COLUMNS, [["", "No SKU", "ea", "stocked", "", ""]])
        importer.commit(admin, kind=ImportKind.PRODUCTS, filename="good.csv", content=good)
        importer.commit(admin, kind=ImportKind.PRODUCTS, filename="bad.csv", content=bad)
        history = importer.history(admin)
        assert {run.status for run in history} == {"committed", "rejected"}
        rejected = next(run for run in history if run.status == "rejected")
        assert rejected.errors
        assert rejected.failure_reason

    def test_re_importing_amends_rather_than_duplicating(
        self, importer: ImportService, admin: Actor, db: Session
    ) -> None:
        first = csv_bytes(PRODUCT_COLUMNS, [["SKU-1", "Original", "ea", "stocked", "", "10"]])
        second = csv_bytes(PRODUCT_COLUMNS, [["SKU-1", "Amended", "ea", "stocked", "", "20"]])
        importer.commit(admin, kind=ImportKind.PRODUCTS, filename="a.csv", content=first)
        report = importer.commit(admin, kind=ImportKind.PRODUCTS, filename="b.csv", content=second)
        assert report.to_amend == 1
        assert db.scalar(select(func.count()).select_from(Product)) == 1
        product = db.scalar(select(Product))
        assert product is not None
        assert product.name == "Amended"

    def test_importing_requires_the_permission(self, importer: ImportService, admin: Actor) -> None:
        weak = make_actor(admin.business_id, permissions={"product.view"})
        with pytest.raises(PermissionDeniedError, match=r"product\.manage"):
            importer.preview(
                weak,
                kind=ImportKind.PRODUCTS,
                filename="x.csv",
                content=csv_bytes(PRODUCT_COLUMNS, product_rows(1)),
            )


class TestParties:
    COLUMNS: ClassVar[list[str]] = [
        "code",
        "name",
        "roles",
        "email",
        "payment_terms",
        "credit_limit",
    ]

    def test_parties_are_imported_with_their_roles(
        self, importer: ImportService, admin: Actor, db: Session, clock: FixedClock
    ) -> None:
        content = csv_bytes(
            self.COLUMNS,
            [
                ["BANHART", "Banhart Hospital", "customer", "a@b.test", "net_30", "50000"],
                ["DCL", "DCL Supplies", "supplier", "s@d.test", "immediate", ""],
                ["BOTH", "Both Ways Ltd", "customer;supplier", "", "eom_15", ""],
            ],
        )
        report = importer.commit(
            admin, kind=ImportKind.PARTIES, filename="parties.csv", content=content
        )
        assert report.committed
        parties = PartyService(db, clock=clock)
        both = parties.by_code(admin, "BOTH")
        assert both is not None
        assert both.is_customer and both.is_supplier
        assert both.payment_term_kind == "end_of_month"
        assert both.payment_term_days == 15

    def test_an_unknown_role_is_refused_with_the_permitted_ones_named(
        self, importer: ImportService, admin: Actor
    ) -> None:
        content = csv_bytes(self.COLUMNS, [["XLTD", "X Ltd", "wholesaler", "", "immediate", ""]])
        report = importer.preview(admin, kind=ImportKind.PARTIES, filename="p.csv", content=content)
        assert report.rejected == 1
        assert "customer, supplier" in report.errors[0].message

    def test_a_repeated_code_within_one_file_is_refused(
        self, importer: ImportService, admin: Actor
    ) -> None:
        content = csv_bytes(
            self.COLUMNS,
            [
                ["DUP", "First", "customer", "", "immediate", ""],
                ["DUP", "Second", "customer", "", "immediate", ""],
            ],
        )
        report = importer.preview(admin, kind=ImportKind.PARTIES, filename="p.csv", content=content)
        assert report.rejected == 1
        assert "more than once" in report.errors[0].message

    def test_a_malformed_payment_term_names_the_permitted_forms(
        self, importer: ImportService, admin: Actor
    ) -> None:
        content = csv_bytes(self.COLUMNS, [["XLTD", "X Ltd", "customer", "", "thirty days", ""]])
        report = importer.preview(admin, kind=ImportKind.PARTIES, filename="p.csv", content=content)
        assert "net_30" in report.errors[0].message

    def test_a_contact_is_created_alongside_the_party(
        self, importer: ImportService, admin: Actor, db: Session, clock: FixedClock
    ) -> None:
        content = csv_bytes(
            ["code", "name", "roles", "contact_name", "contact_email"],
            [["BANHART", "Banhart Hospital", "customer", "A. Mensah", "am@b.test"]],
        )
        importer.commit(admin, kind=ImportKind.PARTIES, filename="p.csv", content=content)
        party = db.scalar(select(Party))
        assert party is not None
        assert [contact.name for contact in party.contacts] == ["A. Mensah"]


class TestBounds:
    def test_a_file_that_is_not_utf8_is_refused_with_a_remedy(
        self, importer: ImportService, admin: Actor
    ) -> None:
        with pytest.raises(ValidationError, match="UTF-8"):
            importer.preview(
                admin,
                kind=ImportKind.PRODUCTS,
                filename="x.csv",
                content=b"sku,name\n\xff\xfe binary",
            )

    def test_a_file_with_no_header_is_refused(self, importer: ImportService, admin: Actor) -> None:
        with pytest.raises(ValidationError, match="header"):
            importer.preview(admin, kind=ImportKind.PRODUCTS, filename="x.csv", content=b"")

    def test_an_oversized_file_is_refused_with_the_limit_named(
        self, importer: ImportService, admin: Actor
    ) -> None:
        with pytest.raises(ValidationError, match="MB"):
            importer.preview(
                admin,
                kind=ImportKind.PRODUCTS,
                filename="x.csv",
                content=b"x" * (17 * 1024 * 1024),
            )


class TestOutcomes:
    def test_outcomes_are_create_amend_or_reject(self) -> None:
        assert {outcome.value for outcome in RowOutcome} == {"create", "amend", "reject"}
