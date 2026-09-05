# Backend services

| Directory | Framework | Responsibility |
| --- | --- | --- |
| [`api/`](api/) | Python, FastAPI, SQLAlchemy | HTTP API, domain model, migrations, provider adapters, and independently runnable workers |

The API, Celery worker, job dispatcher, Kafka outbox relay, analytics consumer, and object cleaner intentionally use the same immutable backend image. Each has a separate command and Kubernetes workload, allowing independent deployment and scaling without duplicating domain code.
