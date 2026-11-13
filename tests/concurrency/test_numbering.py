"""Document numbering under concurrent issue. PRD §9.5, Phase 6 exit criterion 6.

A number that is handed out twice is worse than a number that is refused: two
customers holding the same invoice number is a dispute nobody can settle from the
record. So the test puts several workers on one sequence at once and insists that
every number that came back is distinct, whichever engine is underneath.
"""

from __future__ import annotations

import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING

import pytest
from sqlalchemy.exc import DBAPIError, OperationalError
from sqlalchemy.orm import Session, sessionmaker

from nickland.application.context import Actor
from nickland.application.services.organisation import OrganisationService

# The numbering sequences are keyed on their own enumeration, which names the
# ledger's document kinds rather than the renderer's.
from nickland.domain.organisation import DocumentKind
from nickland.platform.clock import FixedClock

if TYPE_CHECKING:
    from nickland.application.services.organisation import AllocatedNumber

WORKERS = 8
"""Eight at once. More than a counter of this size will ever see, and enough that a
read-then-write race would show on either engine."""


def allocate_in_its_own_session(
    factory: sessionmaker[Session],
    clock: FixedClock,
    actor: Actor,
    kind: str,
    branch_id: uuid.UUID | None,
) -> str | None:
    """One worker's allocation, committed, in a session of its own.

    A serialisation failure returns ``None`` rather than raising: on a snapshot
    isolation level the loser of a race is told to try again, and being told to try
    again is correct behaviour, not a duplicate number. What must never happen is two
    workers committing the same number.
    """
    session = factory()
    try:
        service = OrganisationService(session, clock=clock)
        allocated: AllocatedNumber = service.allocate_number(actor, kind, branch_id=branch_id)
        session.commit()
        return allocated.number
    except (OperationalError, DBAPIError):
        session.rollback()
        return None
    finally:
        session.close()


class TestConcurrentAllocation:
    def test_no_number_is_handed_out_twice(
        self,
        db: Session,
        session_factory: sessionmaker[Session],
        clock: FixedClock,
        admin: Actor,
    ) -> None:
        db.commit()  # The workers read the business through their own connections.
        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            results = list(
                pool.map(
                    lambda _: allocate_in_its_own_session(
                        session_factory, clock, admin, DocumentKind.SALES_INVOICE.value, None
                    ),
                    range(WORKERS),
                )
            )

        issued = [number for number in results if number is not None]
        assert issued, "every worker was refused; none of them allocated a number"
        assert len(set(issued)) == len(issued), f"a number was reused: {sorted(issued)}"

    def test_the_counter_advances_by_exactly_what_was_issued(
        self,
        db: Session,
        session_factory: sessionmaker[Session],
        clock: FixedClock,
        admin: Actor,
    ) -> None:
        db.commit()
        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            results = list(
                pool.map(
                    lambda _: allocate_in_its_own_session(
                        session_factory, clock, admin, DocumentKind.SALES_INVOICE.value, None
                    ),
                    range(WORKERS),
                )
            )
        issued = sorted(number for number in results if number is not None)
        counters = [int(number.rsplit("-", 1)[-1]) for number in issued]
        # Contiguous from one: allocation happens inside the caller's transaction, so a
        # refused worker leaves no gap behind it.
        assert counters == list(range(1, len(counters) + 1))

    def test_two_branches_draw_on_one_sequence_without_colliding(
        self,
        db: Session,
        clock: FixedClock,
        admin: Actor,
        provisioned: dict[str, uuid.UUID],
    ) -> None:
        """A branch has no sequence of its own, so it draws on the business's.

        The branch code goes into the pattern instead. That keeps one counter per
        document type for the whole business, which is what a proprietor reconciling
        a year of waybills wants, and still tells them where each one was written.
        """
        organisation = OrganisationService(db, clock=clock)
        second = organisation.create_branch(admin, code="KSI", name="Kumasi")
        db.flush()
        organisation.amend_sequence(
            admin,
            DocumentKind.WAYBILL.value,
            pattern="{prefix}-{branch}-{counter:4}",
        )
        db.flush()

        accra = [
            organisation.allocate_number(
                admin, DocumentKind.WAYBILL.value, branch_id=provisioned["branch_id"]
            ).number
            for _ in range(3)
        ]
        kumasi = [
            organisation.allocate_number(
                admin, DocumentKind.WAYBILL.value, branch_id=second.id
            ).number
            for _ in range(3)
        ]
        db.flush()
        assert len(set(accra) | set(kumasi)) == 6
        assert all("KSI" in number for number in kumasi)
        assert not any("KSI" in number for number in accra)

    @pytest.mark.usefixtures("sqlite_only")
    def test_a_rolled_back_document_leaves_no_gap(
        self,
        session_factory: sessionmaker[Session],
        clock: FixedClock,
        admin: Actor,
        db: Session,
    ) -> None:
        db.commit()
        abandoned = session_factory()
        try:
            OrganisationService(abandoned, clock=clock).allocate_number(
                admin, DocumentKind.SALES_INVOICE.value, branch_id=None
            )
            abandoned.rollback()
        finally:
            abandoned.close()

        kept = session_factory()
        try:
            number = (
                OrganisationService(kept, clock=clock)
                .allocate_number(admin, DocumentKind.SALES_INVOICE.value, branch_id=None)
                .number
            )
            kept.commit()
        finally:
            kept.close()
        assert number.endswith("0001")
