# ADR-0001 — Python core with an embedded web presentation layer

Status: accepted · Date: 5 November 2026 · Supersedes nothing

## Context

PRD §9.1 confirms a Python-first direction and names React with Tauri as something not to be
used as the core application stack. PRD §9.2 and §9.7 specify PySide6 for the interface. The
summary table in §9 recommends React with TypeScript and Tauri. The three positions cannot
all hold. See [conflict register](../09-conflict-register.md) C-01.

The product also carries an unusually demanding interface for a desktop line-of-business
application: a drag-and-drop document designer (§6.6, designated a core capability), a
latency-sensitive point of sale (§6.10), and dense financial dashboards with drill-down
(§29.1, §29.8). The stated interface standard is high.

## Options considered

**PySide6 throughout.** Honours §9.2 and §9.7 literally. Single language, native Windows
feel, no browser dependency, genuinely offline. Against: the document designer would require
a bespoke canvas and layout engine in Qt; dense dashboards and charts are harder; the styling
system is less expressive than CSS for the standard required; and the pool of people able to
maintain a large QSS-themed Qt application is smaller.

**Python core with an embedded web presentation layer.** Business logic, persistence,
integrations and document rendering stay in Python, satisfying the substance of §9.1. The
interface is HTML and CSS in the Windows system web view. The designer, dashboards and charts
use the medium best suited to them. Against: two languages; a WebView2 runtime dependency;
discipline required to keep business rules out of the frontend.

**React with Tauri.** Explicitly excluded by §9.1.

## Decision

Python core with an embedded web presentation layer.

| Concern | Selection |
| --- | --- |
| Business layer | Python 3.12, FastAPI, SQLAlchemy 2.x, Alembic, Pydantic v2 |
| Desktop host | pywebview over Microsoft Edge WebView2 |
| Presentation | React 18, TypeScript, Vite |
| Document rendering | WeasyPrint, HTML and CSS to PDF |

Tauri is not used, so §9.1's named prohibition is honoured. PySide6 is not used.

## Constraints this decision imposes

1. No business rule exists in the presentation layer. Pricing, tax, commission, allocation,
   scheduling and posting are computed by Python services and transmitted as results. An
   architectural test asserts the frontend contains no calculation of a monetary value.
2. The frontend consumes a generated client built from the OpenAPI schema. The client is not
   hand-written, so a contract change cannot silently diverge.
3. The local service binds to loopback on an ephemeral port and accepts only a token issued by
   the host process at start-up.
4. No asset is fetched at runtime. Fonts, scripts, styles and icons are bundled. The
   application functions with no network interface available.
5. WebView2 is verified present by the installer and provisioned if absent.
6. The same frontend bundle and the same Python services serve both standalone and LAN
   deployment.

## Consequences

**Accepted.** A WebView2 runtime dependency, satisfied by the Evergreen runtime on current
Windows and provisioned by the installer otherwise. Two languages in the repository. A
discipline requirement, enforced by an automated architectural test rather than by review.

**Gained.** The interface standard of §7, §28 and §31 is reachable. The document designer is
built on a medium designed for layout. Charts come from a mature offline library. The §9.3
requirement that future mobile or web clients reuse the Python core without replacement is
satisfied by construction, since the desktop client already consumes the same API.

## Amendment required

PRD §9.2 and §9.7 are revised to specify the embedded web presentation layer. The §9 summary
table's Desktop row is corrected to remove Tauri. §9.1's prohibition is clarified to apply to
the business layer, which it does.
