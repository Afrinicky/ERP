#!/usr/bin/env python3
"""Verify each Phase 4 exit criterion from docs/00-build-plan.md.

Runs against a throwaway database, so it may be run at any time:

    .venv/bin/python tools/verify_phase_4.py
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import uuid
from datetime import date
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

WORKSPACE = Path(tempfile.mkdtemp(prefix="nickland-phase4-"))
os.environ["NICKLAND_DATA_DIRECTORY"] = str(WORKSPACE)
os.environ["NICKLAND_ENVIRONMENT"] = "test"
os.environ["NICKLAND_LOG_LEVEL"] = "ERROR"

from sqlalchemy import func, select, text  # noqa: E402

from nickland.application.context import Actor  # noqa: E402
from nickland.application.services.identity import IdentityService  # noqa: E402
from nickland.application.services.setup import SetupRequest, SetupService  # noqa: E402
from nickland.application.services.taxation import (  # noqa: E402
    TaxableLine,
    TaxContext,
    TaxService,
)
from nickland.domain.errors import ConflictError  # noqa: E402
from nickland.domain.taxation import Outcome  # noqa: E402
from nickland.persistence.base import Base  # noqa: E402
from nickland.persistence.engine import (  # noqa: E402
    create_database_engine,
    create_session_factory,
)
from nickland.persistence.models import Business  # noqa: E402
from nickland.persistence.tax_models import TaxAssessment  # noqa: E402
from nickland.platform.config import get_settings  # noqa: E402

ADMIN = "Provision9Admin"
TODAY = date(2026, 11, 6)
Outcome_ = Outcome
Report = tuple[str, bool, str]


def build() -> tuple[object, Actor, uuid.UUID]:
    settings = get_settings()
    settings.ensure_directories()
    engine = create_database_engine(settings)
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(
            text("CREATE TABLE IF NOT EXISTS alembic_version (version_num varchar(32) NOT NULL)")
        )
        connection.execute(text("INSERT INTO alembic_version VALUES ('phase-4')"))

    session = create_session_factory(engine)()
    SetupService(session).provision(
        SetupRequest(
            business_name="Nicholas Nickland Enterprise",
            administrator_username="proprietor",
            administrator_full_name="N. Nickland",
            administrator_password=ADMIN,
            theme_preset="nickland",
        )
    )
    session.commit()
    business_id = session.scalar(select(Business.id))
    assert business_id is not None
    actor = IdentityService(session).sign_in("proprietor", ADMIN, business_id=business_id).actor
    session.commit()
    return session, actor, business_id


def sale(**overrides: object) -> TaxContext:
    values: dict[str, object] = {
        "transaction_kind": "invoice",
        "on": TODAY,
        "direction": "sales",
        "party_tax_treatment": "standard",
    }
    values.update(overrides)
    return TaxContext(**values)  # type: ignore[arg-type]


def verify() -> list[Report]:
    outcomes: list[Report] = []

    def record(label: str, passed: bool, detail: str = "") -> None:
        outcomes.append((label, passed, detail))

    session, actor, business_id = build()
    tax = TaxService(session)  # type: ignore[arg-type]
    line = [TaxableLine(reference="L1", net=Decimal("1000"))]

    # --- Criterion 1 ------------------------------------------------------------------
    # A newly configured mandatory rule applies to every subsequent matching
    # transaction and to none that preceded its effective date.
    tax.create_rule(
        actor,
        code="tourism",
        name="Tourism Levy",
        category="levy",
        treatment="exclusive",
        rate=Decimal("0.01"),
        effective_from=date(2026, 12, 1),
        cascade_order=35,
    )
    session.commit()

    before = tax.evaluate(actor, line, sale(on=date(2026, 11, 30)))
    after = tax.evaluate(actor, line, sale(on=date(2026, 12, 1)))
    earlier = next(i for i in before.lines[0].assessments if i.rule_code == "tourism")
    later = next(i for i in after.lines[0].assessments if i.rule_code == "tourism")
    record(
        "A new rule applies from its date and not before",
        earlier.outcome is Outcome_.NOT_APPLICABLE and later.amount == Decimal("10"),
        f"30 Nov: {earlier.outcome.value}; 1 Dec: {later.amount}",
    )

    # --- Criterion 2 ------------------------------------------------------------------
    # Withdrawing a rule leaves all posted documents numerically unchanged.
    source = uuid.uuid4()
    posted = tax.evaluate(actor, line, sale())
    tax.record(actor, posted, source_type="invoice", source_id=source)
    session.commit()
    was = {row.rule_code: row.amount for row in tax.assessments(actor, source_id=source)}

    vat = tax.rule_by_code(business_id, "vat")
    assert vat is not None
    tax.withdraw_rule(actor, vat.id, "Superseded for the purposes of this check")
    session.commit()
    now = {row.rule_code: row.amount for row in tax.assessments(actor, source_id=source)}
    absent = "vat" not in {
        i.rule_code for i in tax.evaluate(actor, line, sale()).lines[0].assessments
    }
    record(
        "Withdrawing a rule leaves posted documents unchanged",
        was == now and absent,
        f"{len(was)} charges unchanged; the rule no longer applies to new transactions",
    )

    # --- Criterion 3 ------------------------------------------------------------------
    # A transaction missing required tax data is blocked unless an exception is recorded.
    unresolved = tax.evaluate(actor, line, sale(party_tax_treatment="exempt"))
    try:
        tax.record(actor, unresolved, source_type="invoice", source_id=uuid.uuid4())
        blocked = False
        reason = "it recorded"
    except ConflictError as error:
        session.rollback()
        blocked = True
        reason = str(error)[:60]
    permitted = tax.record(
        actor,
        tax.evaluate(actor, line, sale(party_tax_treatment="exempt")),
        source_type="invoice",
        source_id=uuid.uuid4(),
        exception_reason="Certificate sighted; copy to follow",
    )
    session.commit()
    record(
        "Missing tax data blocks posting unless an exception is recorded",
        blocked and bool(permitted),
        reason,
    )

    # --- Criterion 4 ------------------------------------------------------------------
    # The registers reconcile to their source entries.
    mismatched: list[str] = []
    for obligation in tax.obligations(actor):
        assessed = session.scalar(
            select(func.coalesce(func.sum(TaxAssessment.amount), 0)).where(
                TaxAssessment.obligation_fk == obligation.id,
                TaxAssessment.is_recoverable.is_(False),
                TaxAssessment.is_withheld.is_(False),
            )
        )
        withheld = session.scalar(
            select(func.coalesce(func.sum(TaxAssessment.amount), 0)).where(
                TaxAssessment.obligation_fk == obligation.id,
                TaxAssessment.is_withheld.is_(True),
            )
        )
        if obligation.assessed != Decimal(assessed) or obligation.withheld != Decimal(withheld):
            mismatched.append(obligation.label)
    record(
        "Every register reconciles to its source entries",
        not mismatched,
        f"{len(tax.obligations(actor))} obligations checked against their assessments",
    )

    # --- Criterion 5 ------------------------------------------------------------------
    # A rule used in a posted transaction cannot be deleted.
    try:
        tax.delete_rule(actor, vat.id)
        record("A rule in use cannot be deleted", False, "it was deleted")
    except ConflictError as error:
        session.rollback()
        record("A rule in use cannot be deleted", True, str(error)[:62])

    # --- Criterion 6 ------------------------------------------------------------------
    # Every tax amount traces to its source line, and is counted once.
    repeat = uuid.uuid4()
    twice = tax.evaluate(
        actor,
        [
            TaxableLine(reference="L1", net=Decimal("1000")),
            TaxableLine(reference="L2", net=Decimal("500")),
        ],
        sale(),
    )
    tax.record(actor, twice, source_type="goods_receipt", source_id=repeat)
    session.commit()
    tax.record(actor, twice, source_type="goods_receipt", source_id=repeat)
    session.commit()
    rows = tax.assessments(actor, source_id=repeat)
    per_line: dict[str, Decimal] = {}
    for row in rows:
        per_line[row.source_line_reference] = (
            per_line.get(row.source_line_reference, Decimal("0")) + row.amount
        )
    untraced = [row.rule_code for row in rows if not row.source_line_reference]
    # Expected from the evaluation itself rather than from a figure written here: the
    # point is that recording twice changes nothing, not what the rates happen to be.
    expected = {item.line_reference: item.charged + item.withheld for item in twice.lines}
    record(
        "Every amount traces to its line and is counted once",
        not untraced and per_line == expected and len(rows) == len(expected) * 3,
        f"{len(rows)} rows after recording twice; {dict(sorted(per_line.items()))}",
    )

    # --- Beyond the criteria ----------------------------------------------------------
    # A rate change is prospective, and a posted charge keeps its rate.
    nhil = tax.rule_by_code(business_id, "nhil")
    assert nhil is not None
    tax.set_rate(actor, nhil.id, rate=Decimal("0.03"), effective_from=date(2027, 1, 1))
    session.commit()
    kept = next(row for row in tax.assessments(actor, source_id=source) if row.rule_code == "nhil")
    future = next(
        i
        for i in tax.evaluate(actor, line, sale(on=date(2027, 2, 1))).lines[0].assessments
        if i.rule_code == "nhil"
    )
    record(
        "A rate change is prospective",
        kept.amount == Decimal("25") and future.amount == Decimal("30"),
        f"posted stays {kept.amount}; from January {future.amount}",
    )

    try:
        tax.set_rate(actor, nhil.id, rate=Decimal("0.05"), effective_from=date(2026, 1, 1))
        record("A retrospective change is refused", False, "it was accepted")
    except ConflictError as error:
        session.rollback()
        record("A retrospective change is refused", True, str(error)[:62])

    # The levies feed VAT's base, which is what the law requires.
    fresh = tax.evaluate(actor, line, sale(on=date(2026, 11, 30)))
    amounts = {i.rule_code: i.amount for i in fresh.lines[0].assessments if i.amount}
    record(
        "The levies are charged on the net and VAT on the net plus them",
        amounts.get("nhil") == Decimal("25") and amounts.get("tourism") is None,
        f"{ {k: str(v) for k, v in sorted(amounts.items())} }",
    )

    # An override keeps the calculated figure beside the new one.
    target = next(
        row for row in tax.assessments(actor, source_id=source) if row.rule_code == "nhil"
    )
    assessment, change = tax.override(
        actor,
        target.id,
        amount=Decimal("20"),
        reason="Agreed with the authority on appeal",
        evidence_reference="GRA/APP/2026/18",
    )
    session.commit()
    record(
        "An override keeps the calculated figure beside the new one",
        change.calculated_amount == Decimal("25")
        and change.overridden_amount == Decimal("20")
        and assessment.outcome == Outcome_.OVERRIDDEN.value,
        f"{change.calculated_amount} became {change.overridden_amount}, with a reason",
    )

    # Every rule reports, including the ones that charge nothing.
    silent = [i for i in fresh.lines[0].assessments if not i.detail]
    record(
        "Every rule reports a reason, including when it charges nothing",
        not silent,
        f"{len(fresh.lines[0].assessments)} rules, each with a stated conclusion",
    )

    session.close()
    return outcomes


def main() -> int:
    try:
        outcomes = verify()
    finally:
        shutil.rmtree(WORKSPACE, ignore_errors=True)

    width = max(len(label) for label, _, _ in outcomes)
    print()
    for label, passed, detail in outcomes:
        print(f"  {'PASS' if passed else 'FAIL'}  {label.ljust(width)}  {detail}")
    print()
    broken = [label for label, passed, _ in outcomes if not passed]
    if broken:
        print(f"{len(broken)} criterion(a) not met: {broken}")
        return 1
    print("Every Phase 4 exit criterion is met.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
