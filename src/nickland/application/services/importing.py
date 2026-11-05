"""Validated catalogue and party import.

PRD 9.6 requires that an import be validated, dry-run previewed, bounded in size, and
rolled back entirely on rejection. The preview and the commit run the same validation
over the same rows, so what the preview reports is what the commit does.
"""

from __future__ import annotations

import csv
import hashlib
import io
import uuid
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from nickland.application.audit import AuditRecorder
from nickland.application.context import AnyActor
from nickland.application.services.catalogue import CatalogueService, ProductDraft
from nickland.application.services.parties import PartyDraft, PartyService
from nickland.domain.catalogue import BarcodeKind, ItemClass, TrackingPolicy
from nickland.domain.errors import ConflictError, DomainError, ValidationError
from nickland.domain.parties import PartyType, PaymentTermKind, TaxTreatment
from nickland.persistence.catalogue_models import ImportRun, Product, UnitOfMeasure
from nickland.platform.clock import Clock, SystemClock
from nickland.platform.logging import get_logger

logger = get_logger(__name__)

MAXIMUM_ROWS = 20_000
"""An import is bounded, per PRD 9.6. Larger loads are split."""

MAXIMUM_BYTES = 16 * 1024 * 1024


class ImportKind(StrEnum):
    PRODUCTS = "products"
    PARTIES = "parties"


class RowOutcome(StrEnum):
    CREATE = "create"
    AMEND = "amend"
    REJECT = "reject"


@dataclass(slots=True)
class RowReport:
    """What the import would do with one row, and why."""

    line: int
    outcome: RowOutcome
    reference: str
    message: str = ""
    field: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "line": self.line,
            "outcome": self.outcome.value,
            "reference": self.reference,
            "message": self.message,
            "field": self.field,
        }


@dataclass(slots=True)
class ImportReport:
    """The outcome of a dry run or a commit."""

    kind: ImportKind
    filename: str
    total: int = 0
    to_create: int = 0
    to_amend: int = 0
    rejected: int = 0
    rows: list[RowReport] = field(default_factory=list)
    committed: bool = False
    run_id: uuid.UUID | None = None

    @property
    def acceptable(self) -> bool:
        """Whether the file may be committed.

        An import is all or nothing: one bad row stops the whole file, so a partially
        loaded catalogue cannot result.
        """
        return self.total > 0 and self.rejected == 0

    @property
    def errors(self) -> list[RowReport]:
        return [row for row in self.rows if row.outcome is RowOutcome.REJECT]

    def summary(self) -> str:
        if self.total == 0:
            return "The file contains no data rows."
        if self.rejected:
            return (
                f"{self.rejected} of {self.total} rows cannot be accepted. "
                "Correct them and submit the file again; nothing has been changed."
            )
        return f"{self.total} rows read: {self.to_create} to create, {self.to_amend} to amend."


PRODUCT_COLUMNS = {
    "sku": "The stock-keeping unit. Required. Identifies the row on re-import.",
    "name": "The product name. Required.",
    "item_class": "stocked, non_stock, service or bundle. Defaults to stocked.",
    "category_code": "An existing category code.",
    "stock_unit": "An existing unit code, such as ea. Required for a new product.",
    "sales_unit": "An existing unit code. Defaults to the stock unit.",
    "purchase_unit": "An existing unit code. Defaults to the stock unit.",
    "barcode": "A barcode. Checked against its symbology.",
    "barcode_kind": "ean_13, ean_8, upc_a, code_128, code_39, itf_14 or internal.",
    "manufacturer": "",
    "model_code": "",
    "description": "",
    "list_price": "An indicative price, as a decimal.",
    "tracking_policy": "none, batch or serial. A non-stock item takes none.",
    "requires_expiry": "yes or no. A non-stock item takes no.",
    "shelf_life_days": "A whole number of days.",
    "weight_kg": "A decimal, used where transport is allocated by weight.",
    "volume_m3": "A decimal, used where transport is allocated by volume.",
    "reorder_level": "A decimal.",
    "lead_time_days": "A whole number of days.",
}

PARTY_COLUMNS = {
    "code": "The party code. Identifies the row on re-import. Derived where absent.",
    "name": "The party name. Required.",
    "roles": "customer, supplier or affiliate, separated by semicolons. Required.",
    "legal_name": "",
    "party_type": "organisation or individual. Defaults to organisation.",
    "email": "",
    "telephone": "",
    "tax_identifier": "",
    "tax_treatment": "standard, exempt, zero_rated or withholding_agent.",
    "payment_terms": "immediate, net_30 and so on, or eom_15, or dom_10.",
    "credit_limit": "A decimal.",
    "contact_name": "A primary contact to create alongside the party.",
    "contact_email": "",
    "contact_telephone": "",
    "notes": "",
}


class ImportService:
    """Spreadsheet import, previewed before it commits."""

    def __init__(self, session: DbSession, *, clock: Clock | None = None) -> None:
        self._db = session
        self._clock = clock or SystemClock()
        self._audit = AuditRecorder(session, self._clock)
        self._catalogue = CatalogueService(session, clock=self._clock)
        self._parties = PartyService(session, clock=self._clock)

    def template(self, kind: ImportKind | str) -> str:
        """A template file, with its columns documented in a second row."""
        columns = PRODUCT_COLUMNS if ImportKind(str(kind)) is ImportKind.PRODUCTS else PARTY_COLUMNS
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(columns.keys())
        writer.writerow(columns.values())
        return buffer.getvalue()

    def preview(
        self, actor: AnyActor, *, kind: ImportKind | str, filename: str, content: bytes
    ) -> ImportReport:
        """Validate a file without changing anything, per PRD 9.6."""
        return self._run(actor, kind=kind, filename=filename, content=content, commit=False)

    def commit(
        self, actor: AnyActor, *, kind: ImportKind | str, filename: str, content: bytes
    ) -> ImportReport:
        """Validate and apply a file.

        The validation is the same as the preview's. Where any row is rejected the
        whole file is refused and nothing is written.
        """
        return self._run(actor, kind=kind, filename=filename, content=content, commit=True)

    # -- internals --------------------------------------------------------------------

    def _run(
        self,
        actor: AnyActor,
        *,
        kind: ImportKind | str,
        filename: str,
        content: bytes,
        commit: bool,
    ) -> ImportReport:
        import_kind = ImportKind(str(kind))
        if import_kind is ImportKind.PRODUCTS:
            actor.require("product.manage")
        else:
            actor.require_any("customer.manage", "supplier.manage", "affiliate.manage")

        if len(content) > MAXIMUM_BYTES:
            raise ValidationError(
                f"The file exceeds {MAXIMUM_BYTES // (1024 * 1024)} MB. Split it and "
                "import the parts in turn.",
                field="file",
            )

        rows = self._read(content)
        if len(rows) > MAXIMUM_ROWS:
            raise ValidationError(
                f"The file holds {len(rows):,} rows; the limit is {MAXIMUM_ROWS:,}. "
                "Split it and import the parts in turn.",
                field="file",
            )

        report = ImportReport(kind=import_kind, filename=filename, total=len(rows))
        started = self._clock.now()

        run = ImportRun(
            business_id=actor.business_id,
            kind=import_kind.value,
            filename=filename[:255],
            content_hash=hashlib.sha256(content).hexdigest(),
            status="previewed",
            started_at=started,
            actor_id=actor.user_id,
            total_rows=len(rows),
        )
        self._db.add(run)
        self._db.flush()
        report.run_id = run.id

        # Validate every row first, so the report is complete rather than stopping at
        # the first fault. A user correcting a file wants the whole list.
        context = self._context(actor, import_kind)
        for index, row in enumerate(rows, start=2):
            report.rows.append(self._assess(actor, import_kind, row, index, context))

        report.to_create = sum(1 for row in report.rows if row.outcome is RowOutcome.CREATE)
        report.to_amend = sum(1 for row in report.rows if row.outcome is RowOutcome.AMEND)
        report.rejected = len(report.errors)

        run.accepted_rows = report.to_create + report.to_amend
        run.rejected_rows = report.rejected
        run.errors = [row.as_dict() for row in report.errors[:200]]

        if not commit:
            run.status = "previewed"
            run.completed_at = self._clock.now()
            self._db.flush()
            return report

        if not report.acceptable:
            run.status = "rejected"
            run.completed_at = self._clock.now()
            run.failure_reason = report.summary()
            self._db.flush()
            self._audit.record(
                actor,
                "import_rejected",
                entity_type="import_run",
                entity_id=run.id,
                entity_label=filename,
                context={"rejected": report.rejected, "total": report.total},
            )
            logger.info("Import %s rejected: %s", filename, report.summary())
            return report

        created, amended = self._apply(actor, import_kind, rows)
        report.committed = True
        run.status = "committed"
        run.completed_at = self._clock.now()
        run.created_rows = created
        run.amended_rows = amended
        self._db.flush()

        self._audit.record(
            actor,
            "import_committed",
            entity_type="import_run",
            entity_id=run.id,
            entity_label=filename,
            after={"created": created, "amended": amended, "total": report.total},
        )
        logger.info("Import %s committed: %d created, %d amended", filename, created, amended)
        return report

    def _read(self, content: bytes) -> list[dict[str, str]]:
        """Read a comma-separated file into rows keyed by column name."""
        try:
            text = content.decode("utf-8-sig")
        except UnicodeDecodeError as error:
            raise ValidationError(
                "The file is not readable as UTF-8 text. Save it as CSV (UTF-8) and try again.",
                field="file",
            ) from error

        reader = csv.DictReader(io.StringIO(text))
        if reader.fieldnames is None:
            raise ValidationError("The file has no header row.", field="file")

        headers = [(name or "").strip().lower() for name in reader.fieldnames]
        rows: list[dict[str, str]] = []
        for raw in reader:
            row = {
                header: (value or "").strip()
                for header, value in zip(headers, raw.values(), strict=False)
            }
            # A row of empty cells is a trailing artefact of the spreadsheet, not data.
            if any(row.values()):
                rows.append(row)
        return rows

    def _context(self, actor: AnyActor, kind: ImportKind) -> dict[str, Any]:
        """Look up everything the rows will reference, once rather than per row."""
        if kind is ImportKind.PRODUCTS:
            units = {
                unit.code: unit
                for unit in self._db.scalars(
                    select(UnitOfMeasure).where(UnitOfMeasure.business_id == actor.business_id)
                )
            }
            categories = {
                category.code: category
                for category in self._catalogue.categories(actor, include_inactive=True)
            }
            existing = {
                product.sku: product
                for product in self._db.scalars(
                    select(Product).where(Product.business_id == actor.business_id)
                )
            }
            return {"units": units, "categories": categories, "existing": existing}

        return {
            "existing": {
                party.code: party
                for party in self._parties.search(
                    actor, include_inactive=True, limit=MAXIMUM_ROWS
                ).items
            },
            "seen": set(),
        }

    def _assess(
        self,
        actor: AnyActor,
        kind: ImportKind,
        row: dict[str, str],
        line: int,
        context: dict[str, Any],
    ) -> RowReport:
        """Decide what one row would do, without doing it."""
        try:
            if kind is ImportKind.PRODUCTS:
                return self._assess_product(row, line, context)
            return self._assess_party(row, line, context)
        except ValidationError as error:
            return RowReport(
                line=line,
                outcome=RowOutcome.REJECT,
                reference=row.get("sku") or row.get("code") or row.get("name") or "",
                message=error.message,
                field=error.field,
            )
        except DomainError as error:
            return RowReport(
                line=line,
                outcome=RowOutcome.REJECT,
                reference=row.get("sku") or row.get("code") or row.get("name") or "",
                message=error.message,
            )

    def _assess_product(self, row: dict[str, str], line: int, context: dict[str, Any]) -> RowReport:
        from nickland.domain.catalogue import validate_barcode, validate_sku

        raw_sku = row.get("sku", "")
        if not raw_sku:
            raise ValidationError("A SKU is required.", field="sku")
        sku = validate_sku(raw_sku)

        existing: Product | None = context["existing"].get(sku)
        if not existing and not row.get("name"):
            raise ValidationError("A name is required for a new product.", field="name")

        item_class = _enum(row.get("item_class"), ItemClass, ItemClass.STOCKED, "item_class")
        tracking = _enum(
            row.get("tracking_policy"), TrackingPolicy, TrackingPolicy.NONE, "tracking_policy"
        )
        requires_expiry = _boolean(row.get("requires_expiry"), "requires_expiry")

        from nickland.domain.catalogue import assert_expiry_consistent, assert_tracking_consistent

        assert_tracking_consistent(item_class, tracking)
        assert_expiry_consistent(item_class, requires_expiry)

        if not existing:
            unit_code = row.get("stock_unit") or "ea"
            if unit_code not in context["units"]:
                raise ValidationError(
                    f"Unit {unit_code!r} does not exist. Create it first, or use one of: "
                    f"{', '.join(sorted(context['units'])[:8])}.",
                    field="stock_unit",
                )
        for label in ("sales_unit", "purchase_unit"):
            code = row.get(label)
            if code and code not in context["units"]:
                raise ValidationError(f"Unit {code!r} does not exist.", field=label)

        category_code = row.get("category_code")
        if category_code and category_code not in context["categories"]:
            raise ValidationError(
                f"Category {category_code!r} does not exist. Create it first.",
                field="category_code",
            )

        barcode = row.get("barcode")
        if barcode:
            kind_value = _enum(
                row.get("barcode_kind"), BarcodeKind, BarcodeKind.INTERNAL, "barcode_kind"
            )
            validate_barcode(barcode, kind_value)

        for label in ("list_price", "weight_kg", "volume_m3", "reorder_level"):
            _decimal(row.get(label), label)
        for label in ("shelf_life_days", "lead_time_days"):
            _integer(row.get(label), label)

        return RowReport(
            line=line,
            outcome=RowOutcome.AMEND if existing else RowOutcome.CREATE,
            reference=sku,
            message="Will be amended" if existing else "Will be created",
        )

    def _assess_party(self, row: dict[str, str], line: int, context: dict[str, Any]) -> RowReport:
        from nickland.domain.parties import validate_email, validate_party_code, validate_telephone

        name = row.get("name", "")
        if not name:
            raise ValidationError("A name is required.", field="name")

        roles = {
            role.strip().lower() for role in (row.get("roles") or "").split(";") if role.strip()
        }
        if not roles:
            raise ValidationError(
                "At least one role is required: customer, supplier or affiliate.",
                field="roles",
            )
        unknown = roles - {"customer", "supplier", "affiliate"}
        if unknown:
            raise ValidationError(
                f"Unknown role(s): {', '.join(sorted(unknown))}. Use customer, supplier "
                "or affiliate.",
                field="roles",
            )

        code = validate_party_code(row["code"]) if row.get("code") else None
        if code and code in context["seen"]:
            raise ValidationError(
                f"Code {code!r} appears more than once in this file.", field="code"
            )
        if code:
            context["seen"].add(code)

        _enum(row.get("party_type"), PartyType, PartyType.ORGANISATION, "party_type")
        _enum(row.get("tax_treatment"), TaxTreatment, TaxTreatment.STANDARD, "tax_treatment")
        _payment_terms(row.get("payment_terms"))
        _decimal(row.get("credit_limit"), "credit_limit")
        validate_email(row.get("email"))
        validate_telephone(row.get("telephone"))
        validate_email(row.get("contact_email"), field="contact_email")

        existing = context["existing"].get(code) if code else None
        return RowReport(
            line=line,
            outcome=RowOutcome.AMEND if existing else RowOutcome.CREATE,
            reference=code or name,
            message="Will be amended" if existing else "Will be created",
        )

    def _apply(
        self, actor: AnyActor, kind: ImportKind, rows: list[dict[str, str]]
    ) -> tuple[int, int]:
        """Write the rows. Called only where every row was accepted."""
        created = amended = 0
        context = self._context(actor, kind)

        for row in rows:
            if kind is ImportKind.PRODUCTS:
                was_created = self._write_product(actor, row, context)
            else:
                was_created = self._write_party(actor, row, context)
            created += int(was_created)
            amended += int(not was_created)
        return created, amended

    def _write_product(self, actor: AnyActor, row: dict[str, str], context: dict[str, Any]) -> bool:
        from nickland.domain.catalogue import validate_sku

        sku = validate_sku(row["sku"])
        units: dict[str, UnitOfMeasure] = context["units"]
        categories = context["categories"]
        existing: Product | None = context["existing"].get(sku)

        changes: dict[str, Any] = {}
        for column, attribute in (
            ("name", "name"),
            ("description", "description"),
            ("manufacturer", "manufacturer"),
            ("model_code", "model_code"),
        ):
            if row.get(column):
                changes[attribute] = row[column]
        if row.get("list_price"):
            changes["list_price"] = _decimal(row["list_price"], "list_price")
        if row.get("weight_kg"):
            changes["weight_kg"] = _decimal(row["weight_kg"], "weight_kg")
        if row.get("volume_m3"):
            changes["volume_m3"] = _decimal(row["volume_m3"], "volume_m3")
        if row.get("reorder_level"):
            changes["reorder_level"] = _decimal(row["reorder_level"], "reorder_level")
        if row.get("lead_time_days"):
            changes["lead_time_days"] = _integer(row["lead_time_days"], "lead_time_days")
        if row.get("category_code"):
            changes["category_id"] = categories[row["category_code"]].id

        if existing is not None:
            if changes:
                self._catalogue.amend_product(actor, existing.id, changes)
            return False

        draft = ProductDraft(
            sku=sku,
            name=row.get("name", sku),
            stock_unit_id=units[row.get("stock_unit") or "ea"].id,
            item_class=_enum(row.get("item_class"), ItemClass, ItemClass.STOCKED, "item_class"),
            description=row.get("description") or None,
            category_id=categories[row["category_code"]].id if row.get("category_code") else None,
            sales_unit_id=units[row["sales_unit"]].id if row.get("sales_unit") else None,
            purchase_unit_id=units[row["purchase_unit"]].id if row.get("purchase_unit") else None,
            tracking_policy=_enum(
                row.get("tracking_policy"),
                TrackingPolicy,
                TrackingPolicy.NONE,
                "tracking_policy",
            ),
            requires_expiry=_boolean(row.get("requires_expiry"), "requires_expiry"),
            shelf_life_days=_integer(row.get("shelf_life_days"), "shelf_life_days"),
            manufacturer=row.get("manufacturer") or None,
            model_code=row.get("model_code") or None,
            list_price=_decimal(row.get("list_price"), "list_price"),
            weight_kg=_decimal(row.get("weight_kg"), "weight_kg"),
            volume_m3=_decimal(row.get("volume_m3"), "volume_m3"),
            reorder_level=_decimal(row.get("reorder_level"), "reorder_level"),
            lead_time_days=_integer(row.get("lead_time_days"), "lead_time_days"),
            barcodes=[
                (
                    row["barcode"],
                    _enum(
                        row.get("barcode_kind"),
                        BarcodeKind,
                        BarcodeKind.INTERNAL,
                        "barcode_kind",
                    ),
                )
            ]
            if row.get("barcode")
            else [],
        )
        product = self._catalogue.create_product(actor, draft)
        context["existing"][sku] = product
        return True

    def _write_party(self, actor: AnyActor, row: dict[str, str], context: dict[str, Any]) -> bool:
        from nickland.domain.parties import validate_party_code

        code = validate_party_code(row["code"]) if row.get("code") else None
        existing = context["existing"].get(code) if code else None
        roles = _roles(row.get("roles"))
        kind, days = _payment_terms(row.get("payment_terms"))

        if existing is not None:
            changes: dict[str, Any] = {
                "name": row["name"],
                "is_customer": "customer" in roles,
                "is_supplier": "supplier" in roles,
                "is_affiliate": "affiliate" in roles,
                "payment_term_kind": kind.value,
                "payment_term_days": days,
            }
            for column in ("legal_name", "email", "telephone", "tax_identifier", "notes"):
                if row.get(column):
                    changes[column] = row[column]
            if row.get("credit_limit"):
                changes["credit_limit"] = _decimal(row["credit_limit"], "credit_limit")
            self._parties.amend(actor, existing.id, changes)
            return False

        draft = PartyDraft(
            name=row["name"],
            code=code,
            legal_name=row.get("legal_name") or None,
            party_type=_enum(
                row.get("party_type"), PartyType, PartyType.ORGANISATION, "party_type"
            ),
            is_customer="customer" in roles,
            is_supplier="supplier" in roles,
            is_affiliate="affiliate" in roles,
            email=row.get("email") or None,
            telephone=row.get("telephone") or None,
            tax_identifier=row.get("tax_identifier") or None,
            tax_treatment=_enum(
                row.get("tax_treatment"), TaxTreatment, TaxTreatment.STANDARD, "tax_treatment"
            ),
            payment_term_kind=kind,
            payment_term_days=days,
            credit_limit=_decimal(row.get("credit_limit"), "credit_limit"),
            notes=row.get("notes") or None,
            contacts=[
                {
                    "name": row["contact_name"],
                    "email": row.get("contact_email") or None,
                    "telephone": row.get("contact_telephone") or None,
                    "is_primary": True,
                }
            ]
            if row.get("contact_name")
            else [],
        )
        party = self._parties.create(actor, draft)
        if party.code:
            context["existing"][party.code] = party
        return True

    def history(self, actor: AnyActor, limit: int = 30) -> list[ImportRun]:
        actor.require_any("product.view", "customer.view", "supplier.view")
        return list(
            self._db.scalars(
                select(ImportRun)
                .where(ImportRun.business_id == actor.business_id)
                .order_by(ImportRun.started_at.desc())
                .limit(limit)
            )
        )


# --------------------------------------------------------------------------------------
# Cell readers. Each names the column, so a fault points at a cell rather than a file.
# --------------------------------------------------------------------------------------


def _roles(value: str | None) -> set[str]:
    """Read a semicolon-separated role list."""
    return {role.strip().lower() for role in (value or "").split(";") if role.strip()}


def _enum[EnumT: StrEnum](
    value: str | None, kind: type[EnumT], default: EnumT, field_name: str
) -> EnumT:
    """Read a cell as one of an enumeration's members, naming the permitted values."""
    if value is None or not str(value).strip():
        return default
    candidate = str(value).strip().lower().replace("-", "_").replace(" ", "_")
    try:
        return kind(candidate)
    except ValueError as error:
        permitted = ", ".join(member.value for member in kind)
        raise ValidationError(
            f"{candidate!r} is not valid here. Use one of: {permitted}.",
            field=field_name,
        ) from error


def _decimal(value: str | None, field_name: str) -> Decimal | None:
    if value is None or not str(value).strip():
        return None
    cleaned = str(value).strip().replace(",", "").replace(" ", "")
    try:
        amount = Decimal(cleaned)
    except InvalidOperation as error:
        raise ValidationError(
            f"{value!r} is not a number. Write it as a decimal, such as 1200.50.",
            field=field_name,
        ) from error
    if not amount.is_finite():
        raise ValidationError(f"{value!r} is not a finite number.", field=field_name)
    return amount


def _integer(value: str | None, field_name: str) -> int | None:
    if value is None or not str(value).strip():
        return None
    try:
        return int(str(value).strip())
    except ValueError as error:
        raise ValidationError(f"{value!r} is not a whole number.", field=field_name) from error


def _boolean(value: str | None, field_name: str) -> bool:
    if value is None or not str(value).strip():
        return False
    candidate = str(value).strip().lower()
    if candidate in {"yes", "y", "true", "1"}:
        return True
    if candidate in {"no", "n", "false", "0"}:
        return False
    raise ValidationError(f"{value!r} is not yes or no.", field=field_name)


def _payment_terms(value: str | None) -> tuple[PaymentTermKind, int]:
    """Read a payment term written as ``net_30``, ``eom_15``, ``dom_10`` or ``immediate``."""
    if value is None or not str(value).strip():
        return PaymentTermKind.IMMEDIATE, 0
    candidate = str(value).strip().lower().replace("-", "_").replace(" ", "_")
    if candidate == "immediate":
        return PaymentTermKind.IMMEDIATE, 0

    prefix, _, number = candidate.partition("_")
    mapping = {
        "net": PaymentTermKind.NET_DAYS,
        "eom": PaymentTermKind.END_OF_MONTH,
        "dom": PaymentTermKind.DAY_OF_MONTH,
    }
    if prefix not in mapping or not number.isdigit():
        raise ValidationError(
            f"{value!r} is not a payment term. Write immediate, net_30, eom_15 or dom_10.",
            field="payment_terms",
        )
    return mapping[prefix], int(number)


def assert_not_partially_applied(report: ImportReport) -> None:
    """Guard: a report is either wholly applied or not applied at all."""
    if report.committed and report.rejected:
        raise ConflictError(
            "An import committed with rejected rows. This should be impossible; report it."
        )
