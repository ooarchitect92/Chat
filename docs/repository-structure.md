# Repository structure

```text
frontend/                      React/Vite frontend and Nginx runtime
backend/                       FastAPI domain/API and shared worker image
  src/northstar_api/
    routers/                   HTTP transport
    services/                  domain and provider integrations
    workers/                   independently runnable processes
  migrations/                  Alembic database history
docker/                        Docker operator documentation
kubernetes/                    Kustomize manifests by workload
  overlays/production/         public ingress and environment replacements
database/postgres/             local database bootstrap and roles
infrastructure/                Redis, RabbitMQ, Kafka, and MinIO boundaries
scripts/                       local end-to-end verification
docs/                          architecture, security, and operations guides
.github/                       CI and dependency automation
compose.yaml                   local topology entrypoint
start.bat                      Windows one-click launcher
```

## Dependency direction

1. The web application depends only on the public HTTP API contract.
2. API routers depend on domain services and persistence models.
3. Workers reuse domain services but run as separate processes.
4. Application code does not import deployment definitions.
5. Deployment definitions reference immutable application images.
6. Infrastructure scripts contain no application business logic.

## Runtime ownership

| Process | Entry point | Responsibility | Scaling |
| --- | --- | --- | --- |
| Web | `frontend` | Browser control plane | Horizontal |
| API | `northstar_api.main:app` | HTTP API and coordination | Horizontal |
| Migration | Alembic | PostgreSQL schema | Once per release |
| Celery worker | `workers.celery_app` | RabbitMQ commands | Horizontal |
| Job dispatcher | `workers.job_dispatcher` | Durable jobs to RabbitMQ | Singleton/leader |
| Outbox relay | `workers.outbox_publisher` | Database outbox to Kafka | Singleton/leader |
| Analytics consumer | `workers.analytics_consumer` | Kafka analytics | Consumer group |
| Object cleaner | `workers.object_cleaner` | Kafka cleanup and S3 | Consumer group |

Sharing one backend image preserves identical schemas, encryption, tenant isolation, and provider contracts. Compose and Kubernetes still control each process independently.
