"""Document rendering and issue.

A document is composed as HTML and CSS from the render model and laid out by
WeasyPrint, per the document designer specification. Nothing is fetched while
rendering: fonts are the system's, and the logo arrives embedded.
"""

from nickland.documents.render import (
    RenderedDocument,
    render_html,
    render_pdf,
    render_thermal_text,
)
from nickland.documents.service import DocumentRenderService

__all__ = [
    "DocumentRenderService",
    "RenderedDocument",
    "render_html",
    "render_pdf",
    "render_thermal_text",
]
