"""Party rules: payment terms, credit assessment and identifiers."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from nickland.domain.errors import ValidationError
from nickland.domain.parties import (
    CreditStanding,
    PartyKind,
    PaymentTermKind,
    PaymentTerms,
    TaxTreatment,
    assert_exemption_evidenced,
    assess_credit,
    derive_party_code,
    validate_email,
    validate_party_code,
    validate_telephone,
)

ON = date(2026, 11, 5)


class TestPaymentTerms:
    @pytest.mark.parametrize(
        ("kind", "days", "expected"),
        [
            (PaymentTermKind.IMMEDIATE, 0, date(2026, 11, 5)),
            (PaymentTermKind.NET_DAYS, 30, date(2026, 12, 5)),
            (PaymentTermKind.NET_DAYS, 60, date(2027, 1, 4)),
            (PaymentTermKind.END_OF_MONTH, 0, date(2026, 11, 30)),
            (PaymentTermKind.END_OF_MONTH, 15, date(2026, 12, 15)),
            (PaymentTermKind.DAY_OF_MONTH, 10, date(2026, 12, 10)),
        ],
    )
    def test_due_dates(self, kind: PaymentTermKind, days: int, expected: date) -> None:
        assert PaymentTerms(kind, days).due_date(ON) == expected

    def test_december_end_of_month_does_not_overflow(self) -> None:
        assert PaymentTerms(PaymentTermKind.END_OF_MONTH, 0).due_date(date(2026, 12, 15)) == date(
            2026, 12, 31
        )

    def test_a_negative_term_is_refused(self) -> None:
        with pytest.raises(ValidationError, match="cannot be negative"):
            PaymentTerms(PaymentTermKind.NET_DAYS, -1)

    def test_a_term_beyond_a_year_is_refused(self) -> None:
        with pytest.raises(ValidationError, match="financing agreement"):
            PaymentTerms(PaymentTermKind.NET_DAYS, 400)

    @pytest.mark.parametrize("day", [0, 29, 31])
    def test_a_day_of_month_beyond_twenty_eight_is_refused(self, day: int) -> None:
        """So that the term falls in February as well as in March."""
        with pytest.raises(ValidationError, match="between 1 and 28"):
            PaymentTerms(PaymentTermKind.DAY_OF_MONTH, day)

    def test_descriptions_are_readable(self) -> None:
        assert PaymentTerms(PaymentTermKind.IMMEDIATE).describe() == "Due on issue"
        assert PaymentTerms(PaymentTermKind.NET_DAYS, 30).describe() == "Net 30 days"
        assert PaymentTerms(PaymentTermKind.END_OF_MONTH).describe() == "End of month"


class TestCreditAssessment:
    def test_within_limit(self) -> None:
        assessment = assess_credit(limit=Decimal("10000"), outstanding=Decimal("2000"))
        assert assessment.standing is CreditStanding.WITHIN_LIMIT
        assert assessment.permits_credit_sale
        assert assessment.available == Decimal("8000.0000")

    def test_approaching_limit_still_permits_a_sale(self) -> None:
        assessment = assess_credit(limit=Decimal("10000"), outstanding=Decimal("9500"))
        assert assessment.standing is CreditStanding.APPROACHING_LIMIT
        assert assessment.permits_credit_sale

    def test_over_limit_withholds_credit(self) -> None:
        assessment = assess_credit(limit=Decimal("10000"), outstanding=Decimal("12000"))
        assert assessment.standing is CreditStanding.OVER_LIMIT
        assert not assessment.permits_credit_sale

    def test_an_overdue_balance_withholds_credit_whatever_the_limit(self) -> None:
        """A limit measures exposure; an overdue balance measures conduct."""
        assessment = assess_credit(
            limit=Decimal("100000"), outstanding=Decimal("100"), overdue=Decimal("1")
        )
        assert assessment.standing is CreditStanding.OVERDUE
        assert not assessment.permits_credit_sale

    def test_a_hold_withholds_credit_whatever_the_balance(self) -> None:
        assessment = assess_credit(limit=Decimal("10000"), outstanding=Decimal("0"), on_hold=True)
        assert assessment.standing is CreditStanding.ON_HOLD
        assert not assessment.permits_credit_sale

    @pytest.mark.parametrize("limit", [None, Decimal("0")])
    def test_no_limit_means_cash_only(self, limit: Decimal | None) -> None:
        assessment = assess_credit(limit=limit, outstanding=Decimal("0"))
        assert assessment.standing is CreditStanding.NO_CREDIT
        assert not assessment.permits_credit_sale

    def test_the_detail_names_the_figure(self) -> None:
        assessment = assess_credit(
            limit=Decimal("10000"), outstanding=Decimal("2000"), currency_symbol="GHS "
        )
        assert "GHS 8,000.00" in assessment.detail


class TestIdentifiers:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [("  banhart ", "BANHART"), ("c-nne-0001", "C-NNE-0001"), ("a_b_c", "A-B-C")],
    )
    def test_code_normalisation(self, raw: str, expected: str) -> None:
        assert validate_party_code(raw) == expected

    @pytest.mark.parametrize("raw", ["ab", "-abc", "abc-", "a b!", "x" * 30])
    def test_code_rejection(self, raw: str) -> None:
        with pytest.raises(ValidationError):
            validate_party_code(raw)

    def test_a_code_is_derived_from_a_name(self) -> None:
        assert (
            derive_party_code("Banhart Specialist Hospital", PartyKind.CUSTOMER, 7) == "CBSH-0007"
        )

    def test_a_short_name_still_yields_a_code(self) -> None:
        assert derive_party_code("Q", PartyKind.SUPPLIER, 1).startswith("S")

    @pytest.mark.parametrize("raw", ["a@b.co", "  Accounts@Example.TEST  ", "x.y+z@a.b.c"])
    def test_valid_addresses(self, raw: str) -> None:
        assert validate_email(raw) == raw.strip().lower()

    @pytest.mark.parametrize("raw", ["notanemail", "a@b", "a b@c.d", "@b.co", "a@.co"])
    def test_invalid_addresses(self, raw: str) -> None:
        with pytest.raises(ValidationError, match="not a valid"):
            validate_email(raw)

    def test_an_absent_address_is_permitted(self) -> None:
        assert validate_email(None) is None
        assert validate_email("   ") is None

    def test_a_telephone_retains_the_form_entered(self) -> None:
        assert validate_telephone(" +233 30 000 0000 ") == "+233 30 000 0000"

    @pytest.mark.parametrize("raw", ["12345", "1" * 20])
    def test_an_impossible_telephone_is_refused(self, raw: str) -> None:
        with pytest.raises(ValidationError, match="6 and 18 digits"):
            validate_telephone(raw)


class TestTaxTreatment:
    def test_an_exemption_requires_evidence(self) -> None:
        with pytest.raises(ValidationError, match="certificate"):
            assert_exemption_evidenced(TaxTreatment.EXEMPT, evidence_reference=None)
        with pytest.raises(ValidationError):
            assert_exemption_evidenced(TaxTreatment.EXEMPT, evidence_reference="   ")

    def test_an_evidenced_exemption_is_accepted(self) -> None:
        assert_exemption_evidenced(TaxTreatment.EXEMPT, evidence_reference="CERT-2026-1")

    @pytest.mark.parametrize(
        "treatment",
        [TaxTreatment.STANDARD, TaxTreatment.ZERO_RATED, TaxTreatment.WITHHOLDING_AGENT],
    )
    def test_other_treatments_need_no_evidence(self, treatment: TaxTreatment) -> None:
        assert_exemption_evidenced(treatment, evidence_reference=None)
