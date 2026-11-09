# Demo flow map

A made-up app for the engine's tests.

<!-- placar:inicio -->
| Total | validados | revalidar | sem prova | so em aparelho | quebrados |
|---|---|---|---|---|---|
| **0** | **0** (0%) | 0 | 0 | 0 | 0 |

*Recalculado em 2026-01-01 por `scripts/check_flows.py`. Este placar não é escrito à mão.*
<!-- placar:fim -->

---

| ID | Fluxo | Entrada | Prova | Status | Validado em |
|---|---|---|---|---|---|
| F-RULE-01 | Rules apply | Rules.swift:10 | RulesTests/testA()<br>RulesTests/testB() | validado | 2026-01-10 |
| F-PRICE-01 | Pricing rounds | Rules.swift | Pricing rules/rounds half up | validado | 2026-01-10 |
| F-BARE-01 | Bare name proof | Rules.swift | tag sentence | validado | 2026-01-10 |
| F-HOME-01 | Home opens detail | HomeView.swift:5 | HomeUITests/testOpensDetail() | revalidar | 2026-01-10 |
| F-MIX-01 | Mixed unit and UI | HomeView.swift<br>Rules.swift | RulesTests/testA()<br>HomeUITests/testSaves() | revalidar | 2026-01-10 |
| F-PRINT-01 | Print present | HomeView.swift | print:docs/prints/home.png | validado | 2026-02-01 |
| F-PRINT-02 | Print missing | HomeView.swift | print:docs/prints/missing.png | sem prova | — |
| F-MAN-01 | Manual valid | Rules.swift | manual:2026-02-15:tester | validado | 2026-02-15 |
| F-MAN-02 | Manual expired | Rules.swift | manual:2025-10-01:tester | sem prova | — |
| F-MAN-03 | Manual bad date | Rules.swift | manual:2026-13-01:tester | sem prova | — |
| F-MAN-04 | Manual in the future | Rules.swift | manual:2026-04-15:tester | sem prova | — |
| F-DEV-01 | Device short reason |  | device:camera | sem prova | — |
| F-DEV-02 | Device beside a test |  | device:real camera capture of a receipt<br>RulesTests/testA() | sem prova | — |
| F-DEV-03 | Device good reason |  | device:real camera capture of a receipt | so em aparelho | — |
| F-NOENT-01 | Empty Entry cell |  | RulesTests/testB() | validado | 2026-01-10 |
| F-NOPRF-01 | Empty Proof cell | Rules.swift |  | sem prova | — |
| F-TYPED-01 | Typed validated, state says revalidate | Rules.swift | Pricing rules/loads() | validado | 2026-02-02 |
| F-CAT-01 | Catalog cited | Localizable.xcstrings | Enum rules/enum sentence | validado | 2026-01-10 |
| F-GONE-01 | Cites a file not on disk | Gone.swift:3 | Actor rules/actor sentence | revalidar | 2026-01-10 |

End of the map.
