# Applications

Browser-facing applications live here.

| Directory | Framework | Responsibility |
| --- | --- | --- |
| [`web/`](web/) | React, TypeScript, Vite, Nginx | Operator dashboard, agent builder, integrations, analytics, and hosted chat |

Applications call public service APIs. They must not contain provider secrets, database access, queue consumers, or infrastructure definitions.
