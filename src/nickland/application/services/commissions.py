"""The affiliate commission register, per PRD §6.4 and ADR-0008.

Accrual happens at invoice, not at issue. A quotation may never be accepted, and
accruing against one would put a liability on the register for a sale that has not
happened. The quotation's line-level commission figure is therefore a projection; the
accrual here is the obligation. ``accrue`` is the call the invoicing phase makes, and
it is available now so the register is real rather than scaffolded.

Entitlement follows two independent fractions. The business accrues on what it
actually delivered, and owes on what the customer actually paid: half delivered and
half paid for is a quarter owed, which is what the multiplication in the domain says.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session as DbSession

from nickland.application.audit import AuditRecorder
from nickland.application.context import AnyActor
from nickland.domain.errors import ConflictError, NotFoundError, ValidationError
from nickland.domain.money import ZERO, money, present
from nickland.domain.pricing import (
    CommissionMethod,
    CommissionState,
    assert_commission_transition,
    commission_entitlement,
    validate_rate,
)
from nickland.persistence.base import as_dict
from nickland.persistence.catalogue_models import AffiliateProfile, Party
from nickland.persistence.models import Business
from nickland.persistence.pricing_models import CommissionAccrual, Quotation, QuotationLine
from nickland.platform.clock import Clock, SystemClock, business_date

ONE = Decimal("1")


@dataclass(frozen=True, slots=True)
class RegisterTotals:
    """The register at a glance.

    The first four figures are the accrued value of the entitlements sitting in each
    state: where the obligations have reached. ``settled`` is different in kind — it
    is money that has actually left the business, summed across every state, because
    a payable entitlement that has been half paid has had real money paid against it.
    Reporting the accrued value of fully-settled rows under a heading called "paid"
    would overstate what the business has paid out by every uncollected portion.
    """

    accrued: Decimal
    """Accrued value awaiting approval. Nothing is owed on it yet."""
    approved: Decimal
    """Accrued value approved, but not yet collectible from the customer."""
    payable: Decimal
    """Entitlement less what has been settled, on the payable: what may be paid today.

    Measured as entitlement rather than as accrued value, because a half-collected
    sale has earned the referrer half their commission and a tile reading the whole
    accrual would tell the proprietor to pay twice what is due."""
    cancelled: Decimal
    settled: Decimal
    outstanding: Decimal
    """Entitlement less what has been settled, over the approved and the payable: the
    figure the business owes its referrers in all."""


class CommissionService:
    """Accruals, their states, and the register."""

    def __init__(self, session: DbSession, *, clock: Clock | None = None) -> None:
        self._db = session
        self._clock = clock or SystemClock()
        self._audit = AuditRecorder(session, self._clock)

    # -- accrual ----------------------------------------------------------------------

    def accrue_for_quotation(
        self,
        actor: AnyActor,
        quotation_id: uuid.UUID,
        *,
        source_reference: str,
        fulfilled_fraction: Decimal = ONE,
        on: date | None = None,
    ) -> list[CommissionAccrual]:
        """Accrue the referrer's entitlement on an accepted quotation's lines.

        Only lines the arrangement reached pay anything: a line out of scope produces
        no row, rather than a row of zero, so the register lists obligations and not a
        log of every line ever quoted.
        """
        actor.require("affiliate.manage")
        quotation = self._require_quotation(actor, quotation_id)
        if quotation.affiliate_fk is None:
            raise ConflictError("No referrer is attached to this quotation.")
        if quotation.state not in {"issued", "accepted"}:
            raise ConflictError(
                "Commission accrues on a quotation that has been issued and taken up, "
                "not on a draft."
            )
        fulfilled = validate_rate(fulfilled_fraction, field_name="fulfilled_fraction")
        if fulfilled > ONE:
            raise ValidationError(
                "A fulfilled fraction lies between zero and one.", field="fulfilled_fraction"
            )

        profile = self._db.scalar(
            select(AffiliateProfile).where(
                AffiliateProfile.business_id == actor.business_id,
                AffiliateProfile.party_fk == quotation.affiliate_fk,
                AffiliateProfile.is_active.is_(True),
            )
        )
        accrued_on = on or self._today(actor)
        created: list[CommissionAccrual] = []
        for line in quotation.lines:
            if not line.commission_in_scope or line.commission_amount <= ZERO:
                continue
            existing = self._db.scalar(
                select(CommissionAccrual).where(
                    CommissionAccrual.quotation_line_fk == line.id,
                    CommissionAccrual.affiliate_fk == quotation.affiliate_fk,
                )
            )
            if existing is not None:
                raise ConflictError(
                    f"Line {line.position} has already accrued commission. Amend the "
                    "existing entitlement rather than accruing it twice."
                )
            amount = present(money(line.commission_amount * fulfilled), quotation.precision)
            accrual = CommissionAccrual(
                business_id=actor.business_id,
                affiliate_fk=quotation.affiliate_fk,
                affiliate_profile_fk=profile.id if profile is not None else None,
                quotation_fk=quotation.id,
                quotation_line_fk=line.id,
                source_reference=source_reference,
                state=CommissionState.ACCRUED.value,
                accrued_on=accrued_on,
                basis_amount=line.list_price,
                method=line.commission_method or CommissionMethod.PERCENTAGE.value,
                rate=line.commission_rate,
                accrued_amount=amount,
                fulfilled_fraction=fulfilled,
                paid_fraction=ZERO,
                entitlement=ZERO,
            )
            self._db.add(accrual)
            created.append(accrual)
        self._db.flush()

        self._audit.record(
            actor,
            "commission_accrued",
            entity_type="quotation",
            entity_id=quotation.id,
            entity_label=f"{quotation.number}: {len(created)} accrual(s)",
            before=None,
            after={"source_reference": source_reference, "lines": len(created)},
        )
        return created

    def record_payment_received(
        self,
        actor: AnyActor,
        accrual_id: uuid.UUID,
        *,
        paid_fraction: Decimal,
    ) -> CommissionAccrual:
        """Record how much of the customer's invoice has been collected.

        ADR-0008 makes the entitlement payable on collection, so this is the call that
        moves money towards the referrer. A partial collection produces a partial
        entitlement, which is why the register tracks a fraction and not a flag.
        """
        actor.require("affiliate.manage")
        accrual = self.require(actor, accrual_id)
        fraction = validate_rate(paid_fraction, field_name="paid_fraction")
        if fraction > ONE:
            raise ValidationError(
                "A paid fraction lies between zero and one.", field="paid_fraction"
            )
        if accrual.state in {CommissionState.PAID.value, CommissionState.CANCELLED.value}:
            raise ConflictError(
                f"This entitlement is {accrual.state} and no longer tracks collection."
            )
        if fraction < accrual.paid_fraction:
            raise ValidationError(
                "A collection cannot go backwards. Record a credit note instead, which "
                "cancels the entitlement with a reason.",
                field="paid_fraction",
            )

        before = as_dict(accrual)
        accrual.paid_fraction = fraction
        accrual.entitlement = commission_entitlement(
            accrual.accrued_amount, fulfilled_fraction=ONE, paid_fraction=fraction
        )
        if fraction > ZERO and accrual.state == CommissionState.APPROVED.value:
            accrual.state = CommissionState.PAYABLE.value
        self._db.flush()
        self._audit.record(
            actor,
            "commission_collection_recorded",
            entity_type="commission_accrual",
            entity_id=accrual.id,
            entity_label=f"{fraction} collected",
            before=before,
            after=as_dict(accrual),
        )
        return accrual

    # -- states -----------------------------------------------------------------------

    def approve(self, actor: AnyActor, accrual_id: uuid.UUID) -> CommissionAccrual:
        actor.require("affiliate.manage")
        accrual = self.require(actor, accrual_id)
        assert_commission_transition(CommissionState(accrual.state), CommissionState.APPROVED)
        before = as_dict(accrual)
        accrual.state = CommissionState.APPROVED.value
        accrual.approved_by_id = getattr(actor, "user_id", None)
        accrual.approved_at = self._clock.now()
        if accrual.paid_fraction > ZERO:
            accrual.state = CommissionState.PAYABLE.value
        self._db.flush()
        self._audit.record(
            actor,
            "commission_approved",
            entity_type="commission_accrual",
            entity_id=accrual.id,
            entity_label=str(accrual.accrued_amount),
            before=before,
            after=as_dict(accrual),
        )
        return accrual

    def pay(
        self,
        actor: AnyActor,
        accrual_id: uuid.UUID,
        *,
        amount: Decimal,
        reference: str | None = None,
    ) -> CommissionAccrual:
        """Record a payment to the referrer.

        A payment beyond the entitlement is refused rather than clamped. Paying a
        referrer more than they are owed is a real event with a real cause, and
        silently truncating the figure would hide it.
        """
        actor.require("affiliate.manage")
        accrual = self.require(actor, accrual_id)
        if accrual.state != CommissionState.PAYABLE.value:
            raise ConflictError(
                "Only a payable entitlement can be paid. It becomes payable when the "
                "customer's payment is recorded against the sale it arose on."
            )
        if amount <= ZERO:
            raise ValidationError("A payment is above zero.", field="amount")
        remaining = money(accrual.entitlement - accrual.settled_amount)
        if money(amount) > remaining:
            raise ValidationError(
                f"{amount} exceeds the {remaining} outstanding on this entitlement.",
                field="amount",
            )

        before = as_dict(accrual)
        accrual.settled_amount = money(accrual.settled_amount + amount)
        accrual.payment_reference = reference or accrual.payment_reference
        if accrual.settled_amount >= accrual.accrued_amount:
            # Paid means paid in full, which can only happen once the customer has
            # paid in full: the entitlement grows with collection. Marking the row
            # paid when it merely caught up with a half-collected entitlement would
            # close it, and the second half the referrer earned would have nowhere
            # to go.
            accrual.state = CommissionState.PAID.value
            accrual.paid_at = self._clock.now()
        self._db.flush()
        self._audit.record(
            actor,
            "commission_paid",
            entity_type="commission_accrual",
            entity_id=accrual.id,
            entity_label=f"{amount} to the referrer",
            before=before,
            after=as_dict(accrual),
        )
        return accrual

    def cancel(self, actor: AnyActor, accrual_id: uuid.UUID, *, reason: str) -> CommissionAccrual:
        """Cancel an entitlement, per PRD §6.4: a reversal, a credit note or authority."""
        actor.require("affiliate.manage")
        if not reason.strip():
            raise ValidationError("A cancellation records why.", field="reason")
        accrual = self.require(actor, accrual_id)
        assert_commission_transition(CommissionState(accrual.state), CommissionState.CANCELLED)
        before = as_dict(accrual)
        accrual.state = CommissionState.CANCELLED.value
        accrual.cancellation_reason = reason.strip()
        self._db.flush()
        self._audit.record(
            actor,
            "commission_cancelled",
            entity_type="commission_accrual",
            entity_id=accrual.id,
            entity_label=str(accrual.accrued_amount),
            reason=reason.strip(),
            before=before,
            after=as_dict(accrual),
        )
        return accrual

    def override_rate(
        self,
        actor: AnyActor,
        accrual_id: uuid.UUID,
        *,
        rate: Decimal,
        reason: str,
    ) -> CommissionAccrual:
        """Amend the rate on an accrual, keeping the prior value, per PRD §6.4.

        The previous rate stays on the row. An override that erased what it replaced
        would make the register's own history unreadable, and the control PRD §6.4
        asks for is precisely that the prior value survives.
        """
        actor.require("affiliate.manage")
        if not reason.strip():
            raise ValidationError("An override records why.", field="reason")
        accrual = self.require(actor, accrual_id)
        if accrual.state in {CommissionState.PAID.value, CommissionState.CANCELLED.value}:
            raise ConflictError(f"A {accrual.state} entitlement cannot be re-rated.")
        rate = validate_rate(rate, field_name="rate", ceiling=ONE)

        before = as_dict(accrual)
        accrual.prior_rate = accrual.rate
        accrual.rate = rate
        accrual.accrued_amount = money(accrual.basis_amount * rate)
        accrual.entitlement = commission_entitlement(
            accrual.accrued_amount, fulfilled_fraction=ONE, paid_fraction=accrual.paid_fraction
        )
        accrual.override_reason = reason.strip()
        accrual.overridden_by_id = getattr(actor, "user_id", None)
        accrual.overridden_at = self._clock.now()
        self._db.flush()
        self._audit.record(
            actor,
            "commission_rate_overridden",
            entity_type="commission_accrual",
            entity_id=accrual.id,
            entity_label=f"{accrual.prior_rate} became {rate}",
            reason=reason.strip(),
            before=before,
            after=as_dict(accrual),
        )
        return accrual

    # -- reading ----------------------------------------------------------------------

    def require(self, actor: AnyActor, accrual_id: uuid.UUID) -> CommissionAccrual:
        actor.require_any("affiliate.view", "affiliate.manage")
        accrual = self._db.get(CommissionAccrual, accrual_id)
        if accrual is None or accrual.business_id != actor.business_id:
            raise NotFoundError("Commission entitlement not found.")
        return accrual

    def register(
        self,
        actor: AnyActor,
        *,
        state: str | None = None,
        affiliate_id: uuid.UUID | None = None,
        limit: int = 200,
    ) -> list[CommissionAccrual]:
        actor.require("affiliate.view")
        statement = select(CommissionAccrual).where(
            CommissionAccrual.business_id == actor.business_id
        )
        if state:
            statement = statement.where(CommissionAccrual.state == state)
        if affiliate_id is not None:
            statement = statement.where(CommissionAccrual.affiliate_fk == affiliate_id)
        return list(
            self._db.scalars(statement.order_by(CommissionAccrual.accrued_on.desc()).limit(limit))
        )

    def totals(self, actor: AnyActor) -> RegisterTotals:
        """The register's totals.

        Outstanding counts the approved and the payable, less what has been settled
        against them. An accrual that is merely accrued is not yet an obligation to
        pay anybody, and counting it as one would overstate the liability.
        """
        actor.require("affiliate.view")
        accrued_by_state: dict[str, Decimal] = {}
        entitlement_by_state: dict[str, Decimal] = {}
        settled_by_state: dict[str, Decimal] = {}
        for state, accrued, entitlement, settled in self._db.execute(
            select(
                CommissionAccrual.state,
                func.sum(CommissionAccrual.accrued_amount),
                func.sum(CommissionAccrual.entitlement),
                func.sum(CommissionAccrual.settled_amount),
            )
            .where(CommissionAccrual.business_id == actor.business_id)
            .group_by(CommissionAccrual.state)
        ):
            accrued_by_state[str(state)] = money(accrued or ZERO)
            entitlement_by_state[str(state)] = money(entitlement or ZERO)
            settled_by_state[str(state)] = money(settled or ZERO)

        def unsettled(state: str) -> Decimal:
            return money(entitlement_by_state.get(state, ZERO) - settled_by_state.get(state, ZERO))

        owed = money(
            unsettled(CommissionState.APPROVED.value) + unsettled(CommissionState.PAYABLE.value)
        )
        return RegisterTotals(
            accrued=accrued_by_state.get(CommissionState.ACCRUED.value, ZERO),
            approved=accrued_by_state.get(CommissionState.APPROVED.value, ZERO),
            payable=money(max(unsettled(CommissionState.PAYABLE.value), ZERO)),
            cancelled=accrued_by_state.get(CommissionState.CANCELLED.value, ZERO),
            settled=money(sum(settled_by_state.values(), ZERO)),
            outstanding=money(max(owed, ZERO)),
        )

    def affiliates(self, actor: AnyActor) -> list[Party]:
        actor.require("affiliate.view")
        return list(
            self._db.scalars(
                select(Party)
                .where(Party.business_id == actor.business_id, Party.is_affiliate.is_(True))
                .order_by(Party.name)
            )
        )

    # -- helpers ----------------------------------------------------------------------

    def _require_quotation(self, actor: AnyActor, quotation_id: uuid.UUID) -> Quotation:
        quotation = self._db.get(Quotation, quotation_id)
        if quotation is None or quotation.business_id != actor.business_id:
            raise NotFoundError("Quotation not found.")
        return quotation

    def _require_line(self, actor: AnyActor, line_id: uuid.UUID) -> QuotationLine:
        line = self._db.get(QuotationLine, line_id)
        if line is None or line.business_id != actor.business_id:
            raise NotFoundError("Quotation line not found.")
        return line

    def _today(self, actor: AnyActor) -> date:
        business = self._db.get(Business, actor.business_id)
        timezone = business.timezone if business is not None else "UTC"
        return business_date(self._clock.now(), timezone)
