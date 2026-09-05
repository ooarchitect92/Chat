# Docker Compose

The canonical local topology remains at [`../../compose.yaml`](../../compose.yaml) for compatibility with `start.bat`, CI, and existing operator commands.

It builds `northstar/web` from `apps/web/Dockerfile` and `northstar/api` from `services/api/Dockerfile`. The shared backend image runs separate API, migration, Celery, dispatcher, outbox, analytics, and cleanup processes. PostgreSQL, Redis, RabbitMQ, Kafka, and MinIO remain independent infrastructure containers.
