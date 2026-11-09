# DemoSystem flow map

A made-up docker system for the engine's tests.

<!-- scoreboard:start -->
| Total | validated | revalidate | no proof | device only | broken |
|---|---|---|---|---|---|
| **0** | **0** (0%) | 0 | 0 | 0 | 0 |

*Recomputed on 2026-01-01 by `check-flows`. This scoreboard is not written by hand.*
<!-- scoreboard:end -->

| ID | Flow | Entry | Proof | Status | Validated on |
|---|---|---|---|---|---|
| F-CRY-01 | A key is encrypted | crypto.service.ts | crypto.service.spec/CryptoService > encrypts and decrypts a key | no proof | — |
| F-GAT-01 | The gateway limits calls | gateway.service.ts | gateway.int.spec/gateway > returns 429 with Retry-After when the rate limit is exceeded | no proof | — |
| F-PRV-01 | A provider is created | apps/web/src/app/(dashboard)/providers/page.tsx | providers.e2e/providers > creates a provider | no proof | — |

End.
