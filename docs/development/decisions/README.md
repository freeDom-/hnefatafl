# Architecture decision records

Only decisions with continuing design consequences are recorded here. Each record
states the decision, context, consequences and what would justify revisiting it.

| Record | Decision |
| --- | --- |
| [Authoritative Python engine](adr-001-authoritative-engine.md) | One server-side rules/search authority; the browser never forks game semantics |
| [Analysis execution policy](adr-002-analysis-execution-policy.md) | Deployment, not the player, decides server vs browser analysis |
| [King token design](king-token-design.md) | Selected carved-wood king concept; visual record only |
| [Arena v2 contracts](../arena-contracts.md) | Arena/Workshop shell, theme and surface boundaries |

Historical phase handoffs and superseded proposals are not decision records. They
are preserved by Git history and summarised in release notes where relevant.
