"""The taxation engine against a real database. PRD 19, 21, 24."""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from nickland.application.context import Actor
from nickland.application.services.taxation import (
    SEEDED_RULES,
    TaxableLine,
    TaxContext,
    TaxService,
)
from nickland.domain.errors import (
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
    ValidationError,
)
from nickland.domain.taxation import (
    Condition,
    ConditionField,
    ConditionOperator,
    Outcome,
)
from nickland.persistence.tax_models import TaxAssessment
from nickland.platform.clock import FixedClock
from tests.conftest import make_actor

TODAY = date(2026, 11, 5)


@pytest.fixture
def taxation(db: Session, clock: FixedClock) -> TaxService:
    return TaxService(db, clock=clock)


def sale(**overrides: object) -> TaxContext:
    values: dict[str, object] = {
        "transaction_kind": "invoice",
        "on": TODAY,
        "direction": "sales",
        "party_tax_treatment": "standard",
    }
    values.update(overrides)
    return TaxContext(**values)  # type: ignore[arg-type]


class TestSeeding:
    def test_the_standard_rules_are_seeded_at_provisioning(
        self, taxation: TaxService, admin: Actor
    ) -> None:
        rules = taxation.rules(admin)
        assert len(rules) == len(SEEDED_RULES) == 7
        assert {rule.code for rule in rules} == {
            "nhil",
            "getfund",
            "covid",
            "vat",
            "wht_services",
            "wht_goods",
            "wht_suffered",
        }

    def test_the_revenue_authority_is_seeded(self, taxation: TaxService, admin: Actor) -> None:
        authorities = taxation.authorities(admin)
        assert [item.code for item in authorities] == ["gra"]

    def test_the_levies_run_before_vat_and_vat_compounds(
        self, taxation: TaxService, admin: Actor
    ) -> None:
        order = {rule.code: rule.cascade_order for rule in taxation.rules(admin)}
        assert order["nhil"] < order["vat"]
        assert order["getfund"] < order["vat"]
        assert order["covid"] < order["vat"]
        vat = taxation.rule_by_code(admin.business_id, "vat")  # type: ignore[arg-type]
        assert vat is not None and vat.compounds_on_preceding

    def test_seeding_twice_creates_nothing_further(
        self, taxation: TaxService, admin: Actor
    ) -> None:
        before = len(taxation.rules(admin))
        assert taxation.seed(admin.business_id) == (0, 0)  # type: ignore[arg-type]
        assert len(taxation.rules(admin)) == before


class TestEvaluation:
    def test_a_standard_sale_charges_the_levies_and_vat_on_top_of_them(
        self, taxation: TaxService, admin: Actor
    ) -> None:
        evaluation = taxation.evaluate(
            admin, [TaxableLine(reference="L1", net=Decimal("1000"))], sale()
        )
        amounts = {
            item.rule_code: item.amount
            for item in evaluation.lines[0].assessments
            if item.outcome is Outcome.APPLIED
        }
        assert amounts["nhil"] == Decimal("25")
        assert amounts["getfund"] == Decimal("25")
        assert amounts["covid"] == Decimal("10")
        assert amounts["vat"] == Decimal("159")  # 15% of 1060, not of 1000
        assert evaluation.charged == Decimal("219")
        assert evaluation.withheld == Decimal("0")

    def test_withholding_does_not_apply_to_an_ordinary_customer(
        self, taxation: TaxService, admin: Actor
    ) -> None:
        evaluation = taxation.evaluate(
            admin, [TaxableLine(reference="L1", net=Decimal("1000"))], sale()
        )
        suffered = next(
            item for item in evaluation.lines[0].assessments if item.rule_code == "wht_suffered"
        )
        assert suffered.outcome is Outcome.NOT_APPLICABLE

    def test_a_withholding_agent_deducts_from_what_they_pay_us(
        self, taxation: TaxService, admin: Actor
    ) -> None:
        evaluation = taxation.evaluate(
            admin,
            [TaxableLine(reference="L1", net=Decimal("1000"))],
            sale(party_is_withholding_agent=True),
        )
        assert evaluation.charged == Decimal("219")
        assert evaluation.withheld == Decimal("75")

    def test_a_purchase_of_services_withholds_at_the_service_rate(
        self, taxation: TaxService, admin: Actor
    ) -> None:
        evaluation = taxation.evaluate(
            admin,
            [TaxableLine(reference="P1", net=Decimal("1000"), item_class="service")],
            sale(transaction_kind="supplier_invoice", direction="purchases"),
        )
        amounts = {
            item.rule_code: item.amount
            for item in evaluation.lines[0].assessments
            if item.outcome is Outcome.APPLIED
        }
        assert amounts["wht_services"] == Decimal("75")
        assert "wht_goods" not in amounts

    def test_a_purchase_of_goods_withholds_at_the_goods_rate(
        self, taxation: TaxService, admin: Actor
    ) -> None:
        evaluation = taxation.evaluate(
            admin,
            [TaxableLine(reference="P1", net=Decimal("1000"), item_class="stocked")],
            sale(transaction_kind="supplier_invoice", direction="purchases"),
        )
        amounts = {
            item.rule_code: item.amount
            for item in evaluation.lines[0].assessments
            if item.outcome is Outcome.APPLIED
        }
        assert amounts["wht_goods"] == Decimal("30")
        assert "wht_services" not in amounts

    def test_a_purchase_that_does_not_say_what_it_is_asks_for_review(
        self, taxation: TaxService, admin: Actor
    ) -> None:
        """Goods and services withhold differently; guessing is the thing to avoid."""
        evaluation = taxation.evaluate(
            admin,
            [TaxableLine(reference="P1", net=Decimal("1000"))],
            sale(transaction_kind="supplier_invoice", direction="purchases"),
        )
        unresolved = {item.rule_code for _, item in evaluation.needs_review}
        assert unresolved == {"wht_services", "wht_goods"}

    def test_two_rules_on_the_same_absent_fact_agree_with_each_other(
        self, taxation: TaxService, admin: Actor
    ) -> None:
        """One saying 'not applicable' and the other 'needs review' would be a bug."""
        evaluation = taxation.evaluate(
            admin,
            [TaxableLine(reference="P1", net=Decimal("1000"))],
            sale(transaction_kind="supplier_invoice", direction="purchases"),
        )
        outcomes = {
            item.rule_code: item.outcome
            for item in evaluation.lines[0].assessments
            if item.rule_code in ("wht_services", "wht_goods")
        }
        assert set(outcomes.values()) == {Outcome.NEEDS_REVIEW}

    def test_an_exempt_customer_without_evidence_is_not_relieved(
        self, taxation: TaxService, admin: Actor
    ) -> None:
        evaluation = taxation.evaluate(
            admin,
            [TaxableLine(reference="L1", net=Decimal("1000"))],
            sale(party_tax_treatment="exempt"),
        )
        assert evaluation.needs_review
        assert "no evidence" in evaluation.needs_review[0][1].detail

    def test_an_exempt_customer_with_evidence_is_relieved(
        self, taxation: TaxService, admin: Actor
    ) -> None:
        evaluation = taxation.evaluate(
            admin,
            [TaxableLine(reference="L1", net=Decimal("1000"))],
            sale(party_tax_treatment="exempt", exemption_evidence="EX-2026-0041"),
        )
        assert evaluation.charged == Decimal("0")
        # A rule whose conditions do not match is not applicable, not exempt: relief
        # is from a charge that would otherwise have arisen.
        relieved = [
            item
            for item in evaluation.lines[0].assessments
            if item.outcome is not Outcome.NOT_APPLICABLE
        ]
        assert {item.rule_code for item in relieved} == {"nhil", "getfund", "covid", "vat"}
        assert all(item.outcome is Outcome.EXEMPT for item in relieved)
        assert "EX-2026-0041" in relieved[0].detail

    def test_every_rule_reports_even_when_it_charges_nothing(
        self, taxation: TaxService, admin: Actor
    ) -> None:
        """A tax engine that returns silence is indistinguishable from one that is wrong."""
        evaluation = taxation.evaluate(
            admin, [TaxableLine(reference="L1", net=Decimal("1000"))], sale()
        )
        reported = {item.rule_code for item in evaluation.lines[0].assessments}
        assert reported == {"nhil", "getfund", "covid", "vat", "wht_suffered"}
        assert all(item.detail for item in evaluation.lines[0].assessments)

    def test_a_rule_not_yet_in_force_charges_nothing(
        self, db: Session, taxation: TaxService, admin: Actor
    ) -> None:
        evaluation = taxation.evaluate(
            admin, [TaxableLine(reference="L1", net=Decimal("1000"))], sale(on=date(2025, 6, 1))
        )
        assert evaluation.charged == Decimal("0")
        assert all(
            item.outcome is Outcome.NOT_APPLICABLE for item in evaluation.lines[0].assessments
        )
        del db

    def test_a_condition_decides_whether_a_rule_applies(
        self, db: Session, taxation: TaxService, admin: Actor
    ) -> None:
        vat = taxation.rule_by_code(admin.business_id, "vat")  # type: ignore[arg-type]
        assert vat is not None
        taxation.set_conditions(
            admin,
            vat.id,
            [Condition(ConditionField.PRODUCT_TAX_CODE, ConditionOperator.EQUALS, value="STD")],
        )
        db.flush()

        matching = taxation.evaluate(
            admin,
            [TaxableLine(reference="L1", net=Decimal("1000"), product_tax_code="STD")],
            sale(),
        )
        other = taxation.evaluate(
            admin,
            [TaxableLine(reference="L1", net=Decimal("1000"), product_tax_code="ZERO")],
            sale(),
        )
        assert (
            next(item.outcome for item in matching.lines[0].assessments if item.rule_code == "vat")
            is Outcome.APPLIED
        )
        assert (
            next(item.outcome for item in other.lines[0].assessments if item.rule_code == "vat")
            is Outcome.NOT_APPLICABLE
        )


class TestRecording:
    def test_an_assessment_names_its_rule_version_and_source_line(
        self, db: Session, taxation: TaxService, admin: Actor
    ) -> None:
        source = uuid.uuid4()
        evaluation = taxation.evaluate(
            admin, [TaxableLine(reference="L1", net=Decimal("1000"))], sale()
        )
        written = taxation.record(admin, evaluation, source_type="invoice", source_id=source)
        db.flush()
        vat = next(row for row in written if row.rule_code == "vat")
        assert vat.source_line_reference == "L1"
        assert vat.version_fk is not None
        assert vat.amount == Decimal("159")
        assert vat.assessed_on == TODAY

    def test_a_charge_is_counted_once_however_often_it_is_recorded(
        self, db: Session, taxation: TaxService, admin: Actor
    ) -> None:
        """PRD 24: the supplier invoice entered after the goods receipt must not double."""
        source = uuid.uuid4()
        evaluation = taxation.evaluate(
            admin, [TaxableLine(reference="L1", net=Decimal("1000"))], sale()
        )
        taxation.record(admin, evaluation, source_type="invoice", source_id=source)
        db.flush()
        taxation.record(admin, evaluation, source_type="invoice", source_id=source)
        db.flush()

        rows = db.scalar(
            select(func.count()).select_from(TaxAssessment).where(TaxAssessment.source_id == source)
        )
        total = db.scalar(
            select(func.coalesce(func.sum(TaxAssessment.amount), 0)).where(
                TaxAssessment.source_id == source
            )
        )
        assert rows == 4
        assert Decimal(total) == Decimal("219")

    def test_a_transaction_awaiting_a_decision_cannot_be_recorded(
        self, taxation: TaxService, admin: Actor
    ) -> None:
        evaluation = taxation.evaluate(
            admin,
            [TaxableLine(reference="L1", net=Decimal("1000"))],
            sale(party_tax_treatment="exempt"),
        )
        with pytest.raises(ConflictError, match="unresolved"):
            taxation.record(admin, evaluation, source_type="invoice", source_id=uuid.uuid4())

    def test_an_authorised_exception_lets_it_through_and_is_recorded(
        self, db: Session, taxation: TaxService, admin: Actor
    ) -> None:
        from nickland.persistence.models import AuditEvent

        evaluation = taxation.evaluate(
            admin,
            [TaxableLine(reference="L1", net=Decimal("1000"))],
            sale(party_tax_treatment="exempt"),
        )
        taxation.record(
            admin,
            evaluation,
            source_type="invoice",
            source_id=uuid.uuid4(),
            exception_reason="Certificate sighted; copy to follow",
        )
        db.flush()
        trail = db.scalars(
            select(AuditEvent).where(AuditEvent.action == "tax_exception_recorded")
        ).all()
        assert len(trail) == 1
        assert trail[0].reason == "Certificate sighted; copy to follow"

    def test_an_assessment_traces_back_to_its_line(
        self, db: Session, taxation: TaxService, admin: Actor
    ) -> None:
        source = uuid.uuid4()
        evaluation = taxation.evaluate(
            admin,
            [
                TaxableLine(reference="L1", net=Decimal("1000")),
                TaxableLine(reference="L2", net=Decimal("500")),
            ],
            sale(),
        )
        taxation.record(admin, evaluation, source_type="invoice", source_id=source)
        db.flush()
        rows = taxation.assessments(admin, source_id=source)
        by_line: dict[str, Decimal] = {}
        for row in rows:
            by_line[row.source_line_reference] = (
                by_line.get(row.source_line_reference, Decimal("0")) + row.amount
            )
        assert by_line["L1"] == Decimal("219")
        assert by_line["L2"] == Decimal("109.5")


class TestRateChanges:
    def test_a_change_is_prospective(self, db: Session, taxation: TaxService, admin: Actor) -> None:
        vat = taxation.rule_by_code(admin.business_id, "vat")  # type: ignore[arg-type]
        assert vat is not None
        taxation.set_rate(admin, vat.id, rate=Decimal("0.14"), effective_from=date(2027, 1, 1))
        db.flush()

        before = taxation.evaluate(
            admin, [TaxableLine(reference="L1", net=Decimal("1000"))], sale()
        )
        after = taxation.evaluate(
            admin, [TaxableLine(reference="L1", net=Decimal("1000"))], sale(on=date(2027, 2, 1))
        )
        assert next(
            item.amount for item in before.lines[0].assessments if item.rule_code == "vat"
        ) == Decimal("159")
        assert next(
            item.amount for item in after.lines[0].assessments if item.rule_code == "vat"
        ) == Decimal("148.4")

    def test_a_posted_assessment_is_untouched_by_a_later_change(
        self, db: Session, taxation: TaxService, admin: Actor
    ) -> None:
        source = uuid.uuid4()
        evaluation = taxation.evaluate(
            admin, [TaxableLine(reference="L1", net=Decimal("1000"))], sale()
        )
        taxation.record(admin, evaluation, source_type="invoice", source_id=source)
        db.commit()

        vat = taxation.rule_by_code(admin.business_id, "vat")  # type: ignore[arg-type]
        assert vat is not None
        taxation.set_rate(admin, vat.id, rate=Decimal("0.20"), effective_from=date(2027, 1, 1))
        db.flush()

        posted = next(
            row for row in taxation.assessments(admin, source_id=source) if row.rule_code == "vat"
        )
        assert posted.amount == Decimal("159")

    def test_a_retrospective_change_is_refused_once_the_rule_is_in_use(
        self, db: Session, taxation: TaxService, admin: Actor
    ) -> None:
        vat = taxation.rule_by_code(admin.business_id, "vat")  # type: ignore[arg-type]
        assert vat is not None
        evaluation = taxation.evaluate(
            admin, [TaxableLine(reference="L1", net=Decimal("1000"))], sale()
        )
        taxation.record(admin, evaluation, source_type="invoice", source_id=uuid.uuid4())
        db.commit()

        with pytest.raises(ConflictError, match="past"):
            taxation.set_rate(admin, vat.id, rate=Decimal("0.20"), effective_from=date(2026, 1, 1))

    def test_the_previous_version_is_closed_rather_than_edited(
        self, db: Session, taxation: TaxService, admin: Actor
    ) -> None:
        vat = taxation.rule_by_code(admin.business_id, "vat")  # type: ignore[arg-type]
        assert vat is not None
        original = taxation.version_on(vat, TODAY)
        assert original is not None and original.effective_to is None

        taxation.set_rate(admin, vat.id, rate=Decimal("0.14"), effective_from=date(2027, 1, 1))
        db.flush()
        assert original.rate == Decimal("0.15")
        assert original.effective_to == date(2026, 12, 31)

    def test_two_versions_cannot_begin_on_the_same_day(
        self, db: Session, taxation: TaxService, admin: Actor
    ) -> None:
        vat = taxation.rule_by_code(admin.business_id, "vat")  # type: ignore[arg-type]
        assert vat is not None
        taxation.set_rate(admin, vat.id, rate=Decimal("0.14"), effective_from=date(2027, 1, 1))
        db.flush()
        with pytest.raises(ConflictError, match="already takes effect"):
            taxation.set_rate(admin, vat.id, rate=Decimal("0.13"), effective_from=date(2027, 1, 1))

    def test_a_rate_above_one_is_refused(self, taxation: TaxService, admin: Actor) -> None:
        vat = taxation.rule_by_code(admin.business_id, "vat")  # type: ignore[arg-type]
        assert vat is not None
        with pytest.raises(ValidationError, match="fraction"):
            taxation.set_rate(admin, vat.id, rate=Decimal("15"), effective_from=date(2027, 1, 1))

    def test_changing_a_rate_requires_the_permission(
        self, taxation: TaxService, admin: Actor
    ) -> None:
        vat = taxation.rule_by_code(admin.business_id, "vat")  # type: ignore[arg-type]
        assert vat is not None
        reader = make_actor(admin.business_id, permissions={"tax.view"})  # type: ignore[arg-type]
        with pytest.raises(PermissionDeniedError):
            taxation.set_rate(reader, vat.id, rate=Decimal("0.14"), effective_from=date(2027, 1, 1))


class TestWithdrawal:
    def test_a_withdrawn_rule_stops_applying(
        self, db: Session, taxation: TaxService, admin: Actor
    ) -> None:
        vat = taxation.rule_by_code(admin.business_id, "vat")  # type: ignore[arg-type]
        assert vat is not None
        taxation.withdraw_rule(admin, vat.id, "Superseded")
        db.flush()
        evaluation = taxation.evaluate(
            admin, [TaxableLine(reference="L1", net=Decimal("1000"))], sale()
        )
        assert "vat" not in {item.rule_code for item in evaluation.lines[0].assessments}

    def test_withdrawing_leaves_posted_documents_numerically_unchanged(
        self, db: Session, taxation: TaxService, admin: Actor
    ) -> None:
        source = uuid.uuid4()
        evaluation = taxation.evaluate(
            admin, [TaxableLine(reference="L1", net=Decimal("1000"))], sale()
        )
        taxation.record(admin, evaluation, source_type="invoice", source_id=source)
        db.commit()
        before = {
            row.rule_code: row.amount for row in taxation.assessments(admin, source_id=source)
        }

        vat = taxation.rule_by_code(admin.business_id, "vat")  # type: ignore[arg-type]
        assert vat is not None
        taxation.withdraw_rule(admin, vat.id, "Superseded")
        db.flush()

        after = {row.rule_code: row.amount for row in taxation.assessments(admin, source_id=source)}
        assert after == before

    def test_withdrawal_demands_a_reason(self, taxation: TaxService, admin: Actor) -> None:
        vat = taxation.rule_by_code(admin.business_id, "vat")  # type: ignore[arg-type]
        assert vat is not None
        with pytest.raises(ValidationError, match="reason"):
            taxation.withdraw_rule(admin, vat.id, "  ")

    def test_a_rule_in_use_cannot_be_deleted(
        self, db: Session, taxation: TaxService, admin: Actor
    ) -> None:
        source = uuid.uuid4()
        evaluation = taxation.evaluate(
            admin, [TaxableLine(reference="L1", net=Decimal("1000"))], sale()
        )
        taxation.record(admin, evaluation, source_type="invoice", source_id=source)
        db.commit()

        vat = taxation.rule_by_code(admin.business_id, "vat")  # type: ignore[arg-type]
        assert vat is not None
        with pytest.raises(ConflictError, match="cannot be deleted"):
            taxation.delete_rule(admin, vat.id)

    def test_an_unused_rule_may_be_deleted(
        self, db: Session, taxation: TaxService, admin: Actor
    ) -> None:
        created = taxation.create_rule(
            admin,
            code="tourism",
            name="Tourism Levy",
            category="levy",
            treatment="exclusive",
            rate=Decimal("0.01"),
        )
        db.flush()
        taxation.delete_rule(admin, created.id)
        db.flush()
        assert taxation.rule_by_code(admin.business_id, "tourism") is None  # type: ignore[arg-type]


class TestOverrides:
    def test_an_override_keeps_the_calculated_figure_beside_the_new_one(
        self, db: Session, taxation: TaxService, admin: Actor
    ) -> None:
        source = uuid.uuid4()
        evaluation = taxation.evaluate(
            admin, [TaxableLine(reference="L1", net=Decimal("1000"))], sale()
        )
        written = taxation.record(admin, evaluation, source_type="invoice", source_id=source)
        db.flush()
        vat = next(row for row in written if row.rule_code == "vat")

        assessment, record = taxation.override(
            admin,
            vat.id,
            amount=Decimal("100"),
            reason="Agreed with the authority on appeal",
            evidence_reference="GRA/APP/2026/18",
        )
        db.flush()
        assert record.calculated_amount == Decimal("159")
        assert record.overridden_amount == Decimal("100")
        assert assessment.amount == Decimal("100")
        assert assessment.outcome == Outcome.OVERRIDDEN.value

    def test_an_override_demands_a_reason(
        self, db: Session, taxation: TaxService, admin: Actor
    ) -> None:
        evaluation = taxation.evaluate(
            admin, [TaxableLine(reference="L1", net=Decimal("1000"))], sale()
        )
        written = taxation.record(admin, evaluation, source_type="invoice", source_id=uuid.uuid4())
        db.flush()
        with pytest.raises(ValidationError, match="reason"):
            taxation.override(admin, written[0].id, amount=Decimal("0"), reason="")

    def test_overriding_requires_the_permission(
        self, db: Session, taxation: TaxService, admin: Actor
    ) -> None:
        evaluation = taxation.evaluate(
            admin, [TaxableLine(reference="L1", net=Decimal("1000"))], sale()
        )
        written = taxation.record(admin, evaluation, source_type="invoice", source_id=uuid.uuid4())
        db.flush()
        reader = make_actor(admin.business_id, permissions={"tax.view"})  # type: ignore[arg-type]
        with pytest.raises(PermissionDeniedError):
            taxation.override(reader, written[0].id, amount=Decimal("0"), reason="No")


class TestObligationsAndSettlement:
    def _assessed(self, db: Session, taxation: TaxService, admin: Actor) -> None:
        evaluation = taxation.evaluate(
            admin, [TaxableLine(reference="L1", net=Decimal("1000"))], sale()
        )
        taxation.record(admin, evaluation, source_type="invoice", source_id=uuid.uuid4())
        db.flush()

    def test_an_obligation_opens_for_the_period_a_charge_falls_in(
        self, db: Session, taxation: TaxService, admin: Actor
    ) -> None:
        self._assessed(db, taxation, admin)
        obligations = {item.rule.code: item for item in taxation.obligations(admin)}
        assert set(obligations) == {"nhil", "getfund", "covid", "vat"}
        vat = obligations["vat"]
        assert vat.period_starts_on == date(2026, 11, 1)
        assert vat.period_ends_on == date(2026, 11, 30)
        assert vat.due_on == date(2026, 12, 15)
        assert vat.assessed == Decimal("159")

    def test_an_obligation_reconciles_to_its_own_assessments(
        self, db: Session, taxation: TaxService, admin: Actor
    ) -> None:
        """PRD 19.5: the register and its source must agree, so one is derived."""
        self._assessed(db, taxation, admin)
        for obligation in taxation.obligations(admin):
            total = db.scalar(
                select(func.coalesce(func.sum(TaxAssessment.amount), 0)).where(
                    TaxAssessment.obligation_fk == obligation.id,
                    TaxAssessment.is_recoverable.is_(False),
                    TaxAssessment.is_withheld.is_(False),
                )
            )
            assert obligation.assessed == Decimal(total)

    def test_withholding_suffered_reduces_what_is_payable(
        self, db: Session, taxation: TaxService, admin: Actor
    ) -> None:
        evaluation = taxation.evaluate(
            admin,
            [TaxableLine(reference="L1", net=Decimal("1000"))],
            sale(party_is_withholding_agent=True),
        )
        taxation.record(admin, evaluation, source_type="invoice", source_id=uuid.uuid4())
        db.flush()
        suffered = next(
            item for item in taxation.obligations(admin) if item.rule.code == "wht_suffered"
        )
        assert suffered.withheld == Decimal("75")
        assert taxation.net_payable(suffered) == Decimal("-75")

    def test_a_payment_settles_an_obligation(
        self, db: Session, taxation: TaxService, admin: Actor
    ) -> None:
        self._assessed(db, taxation, admin)
        vat = next(item for item in taxation.obligations(admin) if item.rule.code == "vat")
        payable = taxation.net_payable(vat)

        taxation.record_payment(
            admin,
            reference="GRA-2026-11",
            paid_on=date(2026, 12, 15),
            amount=payable,
            allocations=[(vat.id, payable)],
        )
        db.flush()
        assert vat.state == "paid"
        assert taxation.net_payable(vat) == Decimal("0")

    def test_a_part_payment_says_so(self, db: Session, taxation: TaxService, admin: Actor) -> None:
        self._assessed(db, taxation, admin)
        vat = next(item for item in taxation.obligations(admin) if item.rule.code == "vat")
        taxation.record_payment(
            admin,
            reference="GRA-PART",
            paid_on=date(2026, 12, 15),
            amount=Decimal("100"),
            allocations=[(vat.id, Decimal("100"))],
        )
        db.flush()
        assert vat.state == "part_paid"
        assert taxation.net_payable(vat) == Decimal("59")

    def test_an_unallocated_remainder_stays_unallocated(
        self, db: Session, taxation: TaxService, admin: Actor
    ) -> None:
        self._assessed(db, taxation, admin)
        vat = next(item for item in taxation.obligations(admin) if item.rule.code == "vat")
        payment = taxation.record_payment(
            admin,
            reference="GRA-OVER",
            paid_on=date(2026, 12, 15),
            amount=Decimal("500"),
            allocations=[(vat.id, Decimal("159"))],
        )
        db.flush()
        assert payment.amount == Decimal("500")
        assert payment.allocated == Decimal("159")

    def test_allocating_more_than_was_paid_is_refused(
        self, db: Session, taxation: TaxService, admin: Actor
    ) -> None:
        self._assessed(db, taxation, admin)
        vat = next(item for item in taxation.obligations(admin) if item.rule.code == "vat")
        with pytest.raises(ConflictError, match="exceeds the payment"):
            taxation.record_payment(
                admin,
                reference="GRA-BAD",
                paid_on=date(2026, 12, 15),
                amount=Decimal("50"),
                allocations=[(vat.id, Decimal("159"))],
            )

    def test_a_payment_reference_is_never_reused(
        self, db: Session, taxation: TaxService, admin: Actor
    ) -> None:
        taxation.record_payment(
            admin, reference="GRA-1", paid_on=date(2026, 12, 15), amount=Decimal("10")
        )
        db.flush()
        with pytest.raises(ConflictError, match="already been recorded"):
            taxation.record_payment(
                admin, reference="GRA-1", paid_on=date(2026, 12, 16), amount=Decimal("20")
            )

    def test_recording_a_payment_requires_the_permission(
        self, taxation: TaxService, admin: Actor
    ) -> None:
        reader = make_actor(admin.business_id, permissions={"tax.view"})  # type: ignore[arg-type]
        with pytest.raises(PermissionDeniedError):
            taxation.record_payment(
                reader, reference="X", paid_on=date(2026, 12, 15), amount=Decimal("10")
            )


class TestFilings:
    def test_a_return_opens_against_an_obligation(
        self, db: Session, taxation: TaxService, admin: Actor
    ) -> None:
        evaluation = taxation.evaluate(
            admin, [TaxableLine(reference="L1", net=Decimal("1000"))], sale()
        )
        taxation.record(admin, evaluation, source_type="invoice", source_id=uuid.uuid4())
        db.flush()
        vat = next(item for item in taxation.obligations(admin) if item.rule.code == "vat")

        filing = taxation.open_filing(admin, vat.id)
        db.flush()
        assert filing.state == "due"
        assert filing.declared_amount == Decimal("159")
        assert filing.due_on == vat.due_on

    def test_opening_twice_returns_the_same_return(
        self, db: Session, taxation: TaxService, admin: Actor
    ) -> None:
        evaluation = taxation.evaluate(
            admin, [TaxableLine(reference="L1", net=Decimal("1000"))], sale()
        )
        taxation.record(admin, evaluation, source_type="invoice", source_id=uuid.uuid4())
        db.flush()
        vat = next(item for item in taxation.obligations(admin) if item.rule.code == "vat")
        assert taxation.open_filing(admin, vat.id).id == taxation.open_filing(admin, vat.id).id

    def test_a_submitted_return_is_not_restated(
        self, db: Session, taxation: TaxService, admin: Actor
    ) -> None:
        evaluation = taxation.evaluate(
            admin, [TaxableLine(reference="L1", net=Decimal("1000"))], sale()
        )
        taxation.record(admin, evaluation, source_type="invoice", source_id=uuid.uuid4())
        db.flush()
        vat = next(item for item in taxation.obligations(admin) if item.rule.code == "vat")
        filing = taxation.open_filing(admin, vat.id)
        taxation.submit_filing(admin, filing.id, reference="VAT-2026-11")
        db.flush()
        with pytest.raises(ConflictError, match="already"):
            taxation.submit_filing(admin, filing.id, reference="VAT-2026-11-A")


class TestScope:
    def test_a_rule_from_another_business_is_not_found(
        self, taxation: TaxService, admin: Actor
    ) -> None:
        vat = taxation.rule_by_code(admin.business_id, "vat")  # type: ignore[arg-type]
        assert vat is not None
        stranger = make_actor(uuid.uuid4())
        with pytest.raises(NotFoundError):
            taxation.require_rule(stranger, vat.id)

    def test_reading_requires_the_permission(self, taxation: TaxService, admin: Actor) -> None:
        nobody = make_actor(admin.business_id, permissions=set())  # type: ignore[arg-type]
        with pytest.raises(PermissionDeniedError):
            taxation.rules(nobody)
