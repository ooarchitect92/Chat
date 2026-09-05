# Frontend

Browser-facing source lives in [`../frontend/`](../frontend/).

| Directory | Framework | Responsibility |
| --- | --- | --- |
| [`frontend/`](../frontend/) | React, TypeScript, Vite, Nginx | Operator dashboard, agent builder, integrations, analytics, and hosted chat |

Applications call public service APIs. They must not contain provider secrets, database access, queue consumers, or infrastructure definitions.
