# Module Specifications

Per-module specifications are written at the commencement of the phase that delivers the
module, not in advance. Writing them now would fix detail that the preceding phases will
inform.

Each specification states the module's entities, services, state machines, permissions,
validation rules, events emitted and consumed, interface screens and test obligations. The
[build plan](../00-build-plan.md) states which specifications each phase requires.

| Phase | Specifications required |
| --- | --- |
| 1 | `platform`, `identity`, `organisation` |
| 2 | `catalogue`, `parties` |
| 3 | `ledger` |
| 4 | `taxation` |
| 5 | `pricing`, `sales` (quotation) |
| 6 | `documents` |
| 7 | `sales` (orders), `procurement` |
| 8 | `inventory` |
| 9 | `dispatch` |
| 10 | `pos` |
| 11 | `finance` |
| 12 | `financing` |
| 13 | `analytics` (budgets, forecasting, profitability) |
| 14 | `analytics` (reporting, period close) |
| 15 | LAN deployment specification |
| 16 | External API specification |

The following are already specified in full and require no further module document:

- Pricing — [pricing engine](../04-pricing-engine.md)
- Ledger posting rules — [accounting model](../05-accounting-model.md)
- Documents — [document designer](../06-document-designer.md)
