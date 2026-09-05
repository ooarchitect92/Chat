# Operations

## Compose services

| Service | Purpose | Startup gate |
| --- | --- | --- |
| `postgres` | PostgreSQL 16 with pgvector | `pg_isready` |
| `redis` | Rate limits, access revocation, refresh-family rotation | Authenticated `PING` |
| `rabbitmq` | Celery command broker and management UI | Broker diagnostic ping |
| `kafka` | Single-node KRaft event log | Broker metadata/list operation |
| `minio` | S3-compatible knowledge storage | MinIO live endpoint |
| `minio-init` | Creates, privatizes, and versions the knowledge bucket | Successful one-shot exit |
| `migrate` | Applies Alembic migrations | Successful one-shot exit |
| `api` | FastAPI HTTP/SSE service | `/health/ready` |
| `worker` | Celery background jobs | Required stores/broker healthy |
| `job-dispatcher` | Publishes persisted queued-ingestion jobs to RabbitMQ | Database migration and RabbitMQ ready |
| `outbox-relay` | Publishes committed outbox events | Database migration and Kafka ready |
| `analytics-consumer` | Consumes Kafka and updates projections | Database migration and Kafka ready |
| `object-cleaner` | Purges deleted knowledge-object versions from storage | Database migration, Kafka, and bucket initialization ready |
| `web` | Nginx + React application | API healthy and `/healthz` succeeds |

`docker compose up --build --detach` starts the entire integration topology. Dependencies use health or successful-completion conditions. The API is gated on migration and Redis, while queue, event, and object-storage processes have their own gates; this preserves the documented degradation boundary when an asynchronous subsystem is unavailable. The worker receives a dedicated non-internal `egress` bridge so URL ingestion and NVIDIA embedding calls work locally; production must replace unrestricted outbound access with DNS/IP-aware proxy or network policy controls.

## Lifecycle

```bash
docker compose config --quiet
docker compose up --build --detach
bash scripts/smoke.sh
docker compose logs --follow --tail=200 api worker job-dispatcher outbox-relay analytics-consumer object-cleaner
docker compose down
```

On Windows, use `./scripts/smoke.ps1`. `docker compose down` preserves named volumes. `make reset-data` removes all local database, broker, and object data and is intentionally destructive.

## Health semantics

- **Liveness** answers whether the process/event loop can serve work. It should not fail for a short dependency outage.
- **Readiness** answers whether the instance should receive new traffic. It checks critical dependencies with short deadlines.
- Worker/broker backlog is monitored through metrics and management APIs; it is not inferred solely from a running process.

The smoke scripts first verify infrastructure health and one-shot initialization, confirm that the documented browser origin is allowed by object-store CORS while an untrusted origin is denied, then wait for API, Nginx, and Celery health and assert that the dispatcher and Kafka consumers are running. On failure they print bounded service logs without exposing environment values. When testing overridden endpoints, set `WEB_URL`, `API_URL`, `OBJECT_STORE_URL`, and `SMOKE_WEB_ORIGIN` for the script as needed.

Set both `SMOKE_ADMIN_EMAIL` and `SMOKE_ADMIN_PASSWORD` to enable the authenticated storage probe. It logs in through Nginx, computes SHA-256 for the probe bytes, requests a tenant-scoped presigned POST with the required digest, submits every signed multipart field with the identical file part last, creates a file knowledge source, waits for checksum-verified staging promotion and ingestion, and deletes the temporary source. The check is skipped when neither credential is supplied and fails fast when only one is supplied. Use a dedicated disposable smoke identity and inject its password at runtime; never put it in source or CI logs.

## Observability

Collect container stdout as structured JSON and scrape `/metrics`. At minimum alert on:

- API error/latency/SSE disconnect rates and saturation.
- Database pool exhaustion, lock waits, slow queries, replication lag, and disk growth.
- RabbitMQ ready/unacked messages, oldest-message age, redeliveries, dead letters, disk alarms, and worker heartbeats.
- Kafka consumer lag, under-replicated partitions, offline partitions, outbox backlog count/age, and publish failures.
- Event-quarantine growth, object-cleaner retry age, and deletion-event lag.
- Redis memory, evictions, blocked clients, rejected connections, rate-limit errors, and session-store failures.
- Object-store capacity, request errors, failed promotions/purges, and lifecycle/replication failures.
- NVIDIA request latency, provider error codes, token use, and cost—without logging prompt bodies.
- Retrieval no-hit rate, cited-answer rate, abstention rate, escalation rate, and evaluated answer quality.

Use request IDs across HTTP logs and correlation/event IDs across asynchronous messages. Avoid high-cardinality IDs in metric labels.

## Reliability contracts

### Background commands

RabbitMQ/Celery delivery is at least once. The API first commits an ingestion job in PostgreSQL; `job-dispatcher` claims undispatched jobs and retries broker publication, so a brief RabbitMQ outage does not strand an acknowledged API request. Task code must accept duplicate delivery and persist progress against a stable job/idempotency key. Retry only transient failures, use exponential backoff plus jitter, and cap attempts. Permanent validation/exhausted failures transition the persisted job to a terminal state. The ingestion queue declares the durable `ingest.failed` dead-letter route for broker-rejected/dead-lettered messages; do not assume a normally acknowledged Celery failure is copied there.

Scale workers by adding replicas, not by running multiple schedulers accidentally. Separate queues/concurrency for CPU-heavy extraction and provider-bound embedding when workloads grow.

### Outbox and events

Monitor the oldest unpublished outbox row, not only row count. If Kafka is unavailable, committed product operations remain in PostgreSQL and the relay retries. Valid consumers write idempotently by event ID; offset commits occur only after the projection or cleanup completes. An invalid envelope or payload is written to `event_quarantine` with its Kafka coordinates, payload SHA-256, redacted bounded excerpt, and validation error before that poison offset is committed. Operational failures are not quarantined: the consumer seeks back and retries.

Deleting a file knowledge source commits `knowledge.source.deleted.v1`. The `object-cleaner` validates that `objectKey` is exactly under `knowledge/{tenantId}/`, deduplicates by event ID, and purges every version and delete marker for that key. Purge occurs before the processed marker, so a crash can repeat only the idempotent deletion. Monitor its consumer lag: database deletion succeeds before asynchronous object cleanup.

Schema changes are backward compatible during rolling deployments. Add fields before requiring them, version semantic changes, and retain old consumers until lag reaches zero.

### Redis

Redis keys require explicit TTLs. Production uses authentication/TLS, persistence appropriate to refresh-family/access-revocation lifetimes, plus a no-eviction policy for live security keys. Redis is neither a retrieval cache nor a Celery result backend. `RATE_LIMIT_FAIL_OPEN=false` makes an availability outage fail closed, but it cannot recover an evicted/lost rate window or access revocation; verify persistence and restore behavior. Lost refresh-family keys invalidate those sessions.

## Database migrations

The `migrate` service runs before API and worker startup. For releases:

1. Back up and verify available disk/replication health.
2. Review generated SQL and lock/rewrite risk.
3. Apply backward-compatible expansion migrations.
4. Deploy code that can read old and new shapes.
5. Backfill asynchronously in bounded batches.
6. Remove old fields only in a later release after rollback is no longer required.

Do not run destructive schema changes automatically with every replica. Production should use a single controlled migration job and a database role distinct from the runtime role.

Compose demonstrates three database identities:

- `northstar` is the schema owner used only by the one-shot `migrate` service.
- `northstar_app` is the API login. Transactions switch to this tenant-RLS role.
- `northstar_service` is the non-owner background login with `BYPASSRLS`, required for cross-tenant queue and outbox scans. Background containers set `DATABASE_APPLY_RUNTIME_ROLE=false` so they do not shed that privilege by switching to the API role.

Keep `POSTGRES_PASSWORD`, `POSTGRES_RUNTIME_PASSWORD`, and `POSTGRES_SERVICE_PASSWORD` distinct, and keep each corresponding DSN synchronized. Role initialization only occurs when PostgreSQL creates a fresh data directory; changing these variables does not rotate credentials in an existing volume. Use controlled `ALTER ROLE` operations and update the secret store/DSNs together for rotation.

## Backup and restore

Back up PostgreSQL with point-in-time recovery and the object store with versioning/replication. Redis, RabbitMQ, and Kafka durability settings reduce outage loss but do not replace product-data backups. The authoritative reconstruction inputs are PostgreSQL, source objects, and retained event history where applicable.

A restore exercise should verify:

- Tenant, agent, knowledge metadata, vectors, conversations, and outbox consistency.
- Source objects referenced by database rows exist and are readable.
- Consumers can resume or rebuild projections without duplicating visible data.
- Rotated secrets and external provider configuration are restored from the secret manager, not backups of `.env`.

Document recovery-point and recovery-time objectives and test them on a schedule.

## Common incidents

### API never becomes ready

Run `docker compose ps` and inspect `migrate`, `postgres`, and API logs. A failed migration remains visible as a completed non-zero container. Confirm all database URLs use the Compose hostname `postgres`, not `localhost`.

### Knowledge remains queued

Check the `job-dispatcher` and Celery worker logs, RabbitMQ health and queue age/depth, object access, and provider errors. Before retrying manually, identify the persisted job key so duplicate work remains safe.

If the browser cannot upload a newly selected file, confirm it supplied the required SHA-256 of the exact bytes, copied every returned multipart field without converting fields to headers, and appended the file last. Confirm `S3_PUBLIC_ENDPOINT_URL` is reachable from the browser and that its host, scheme, and port match the presigned URL. Promotion rejects changed/mismatched size, type, checksum, ETag, or object version. If ingestion fails after promotion, confirm the worker can reach `S3_ENDPOINT_URL`. Both endpoint settings must address the same bucket and credentials.

### Analytics is stale

Compare unpublished outbox age with Kafka consumer lag. Outbox growth means publishing is blocked; lag growth means the consumer or projection database is slow. Restarting is safe only because both stages are idempotent.

### Deleted knowledge objects remain in storage

Check `object-cleaner` logs and lag for `knowledge.source.deleted.v1`. A malformed event is recorded in `event_quarantine`; an object-store or database failure leaves the offset uncommitted and retries with bounded exponential delay. Do not manually delete a broad tenant prefix—cleanup intentionally accepts only the exact tenant-prefixed object key carried by a validated event.

### Chat fails but administration works

Check provider configuration/connectivity, model timeout/error metrics, rate limiting, and retrieval health. Do not enable browser demo fallback in production to hide the failure.

### Embedded widget is blank

Confirm `/widget/{publicId}` does not return the admin SPA's `X-Frame-Options: SAMEORIGIN`, and check the browser console for a `frame-ancestors` violation. Verify the customer top-page origin is present in the agent allowlist and that `public/widget.js` can call bootstrap/session routes directly from that page; there is no forwarded-origin header or query parameter. On first open, the iframe must send `northstar:ready`, and the loader must validate its source/platform origin before creating a session and replying with a platform-targeted `northstar:init`. Check CSP, CORS, blocked third-party scripts, and `postMessage` errors. Production edges that generate per-agent CSP must keep that policy synchronized with the API allowlist.

### Port conflict

Override the relevant host variable in `.env` (`WEB_PORT`, `API_PORT`, `POSTGRES_PORT`, `REDIS_PORT`, `RABBITMQ_MANAGEMENT_PORT`, `KAFKA_HOST_PORT`, or the MinIO ports). Container-to-container URLs and ports remain unchanged. If the web origin changes, also update `APP_CORS_ORIGINS`, `MINIO_API_CORS_ALLOW_ORIGIN`, and `S3_PUBLIC_ENDPOINT_URL` where applicable.

## Production transition

The Compose Kafka broker is single-node with replication factor one, and every local management port plus the `local-access` and unrestricted `egress` bridges is intended only for development. Production requires removal of those host publications, policy-controlled outbound access, clustered/managed stateful services, TLS and workload identities, explicit topic/queue policies, multi-zone placement, resource requests/limits, autoscaling, disruption budgets, ingress rate limiting, immutable image digests, and tested disaster recovery.
