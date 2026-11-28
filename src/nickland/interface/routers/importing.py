"""Import endpoints. A file is previewed before it commits, per PRD 9.6."""

from __future__ import annotations

from fastapi import APIRouter, Response

from nickland.interface.catalogue_schemas import (
    ImportContract,
    ImportReportReply,
    ImportRunReply,
    RowReportReply,
)
from nickland.interface.dependencies import ActorDependency, ImportDependency

router = APIRouter(prefix="/imports", tags=["import"])


def _report_reply(report: object) -> ImportReportReply:
    return ImportReportReply(
        kind=report.kind.value,  # type: ignore[attr-defined]
        filename=report.filename,  # type: ignore[attr-defined]
        total=report.total,  # type: ignore[attr-defined]
        to_create=report.to_create,  # type: ignore[attr-defined]
        to_amend=report.to_amend,  # type: ignore[attr-defined]
        rejected=report.rejected,  # type: ignore[attr-defined]
        acceptable=report.acceptable,  # type: ignore[attr-defined]
        committed=report.committed,  # type: ignore[attr-defined]
        summary=report.summary(),  # type: ignore[attr-defined]
        run_id=report.run_id,  # type: ignore[attr-defined]
        rows=[
            RowReportReply(
                line=row.line,
                outcome=row.outcome.value,
                reference=row.reference,
                message=row.message,
                field=row.field,
            )
            # The whole file's faults are reported, bounded so one bad file cannot
            # produce an unbounded response.
            for row in report.rows[:500]  # type: ignore[attr-defined]
        ],
    )


@router.get("/template/{kind}", summary="Download an import template")
def template(kind: str, actor: ActorDependency, importer: ImportDependency) -> Response:
    """A template file, with each column documented in its second row."""
    actor.require_any("product.view", "customer.view", "supplier.view")
    body = importer.template(kind)
    return Response(
        content=body,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="nickland-{kind}.csv"'},
    )


@router.post("/preview", response_model=ImportReportReply, summary="Preview an import")
def preview(
    contract: ImportContract, actor: ActorDependency, importer: ImportDependency
) -> ImportReportReply:
    """Validate a file without changing anything.

    Every row is assessed, so the report lists all faults rather than stopping at the
    first.
    """
    return _report_reply(
        importer.preview(
            actor,
            kind=contract.kind,
            filename=contract.filename,
            content=contract.content(),
        )
    )


@router.post("/commit", response_model=ImportReportReply, summary="Apply an import")
def commit(
    contract: ImportContract, actor: ActorDependency, importer: ImportDependency
) -> ImportReportReply:
    """Validate and apply a file.

    An import is all or nothing: where any row is rejected the whole file is refused
    and nothing is written.
    """
    return _report_reply(
        importer.commit(
            actor,
            kind=contract.kind,
            filename=contract.filename,
            content=contract.content(),
        )
    )


@router.get("", response_model=list[ImportRunReply], summary="List past imports")
def history(
    actor: ActorDependency, importer: ImportDependency, limit: int = 30
) -> list[ImportRunReply]:
    return [ImportRunReply.model_validate(run) for run in importer.history(actor, limit)]
