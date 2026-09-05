# Infrastructure services

Local infrastructure is orchestrated from the root `compose.yaml`. Each directory documents one independently replaceable service and its production boundary.

- `redis/` - cache, rate limits, and transient coordination
- `rabbitmq/` - Celery command queue
- `kafka/` - durable event stream and consumers
- `minio/` - local S3-compatible object storage

PostgreSQL bootstrap files live in `database/postgres/` because they are versioned database assets. Kubernetes manifests live separately in `kubernetes/`.
