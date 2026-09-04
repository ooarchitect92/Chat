# Northstar API

FastAPI backend for a multi-tenant, grounded AI support platform. PostgreSQL is the source of truth; pgvector `halfvec` search and PostgreSQL full-text search provide reciprocal-rank-fused retrieval. Redis holds rate limits, access revocations, and one-time refresh-family state—not retrieval results or Celery results. RabbitMQ runs ingestion jobs, and a transactional outbox feeds Kafka analytics and knowledge-object cleanup consumers.

## Local development

```bash
cp .env.example .env
uv sync --all-groups --locked
# From the repository root, run migrations with the schema-owner identity:
docker compose run --rm migrate
uv run uvicorn northstar_api.main:app --reload
```

The service-level example uses host-reachable `localhost` ports and the placeholder credentials from the root Compose example. Start the required stores from the repository root first, or replace those URLs with your own services. Container processes instead receive Compose-internal hostnames from the root `compose.yaml`. Do not run Alembic with the API's restricted `DATABASE_URL`; use the one-shot Compose migration service or explicitly supply the schema-owner `MIGRATION_DATABASE_URL` as `DATABASE_URL` for the migration command.

For disposable local development, set `SEED_ADMIN_EMAIL` and `SEED_ADMIN_PASSWORD` together to create the first owner; `SEED_DEMO_AGENT=true` adds a sample agent only when that new owner is created. There are no built-in credentials. Do not use the environment seeder as the normal production provisioning path: leave the credentials unset with `SEED_DEMO_AGENT=false` and `ALLOW_PRODUCTION_SEED=false`. Production rejects a seed password unless the explicit override is enabled and the password is at least 12 characters without `change-me`.

The dashboard uses the canonical `/api/v1` base; `/v1` remains a compatibility alias for non-browser clients. OpenAPI is at `/docs` outside production. Liveness, readiness and Prometheus endpoints are `/health/live`, `/health/ready`, and `/metrics`.

## Browser authentication

Production login/refresh set a host-only `northstar_refresh` cookie with `HttpOnly`, `SameSite=Strict`, `Secure`, refresh-TTL `Max-Age`, and path `${APP_API_PREFIX}/auth`. Production session JSON does not expose the raw refresh token; non-production/test responses and optional refresh/logout bodies retain it for CLI compatibility. Redis records one valid JTI per refresh family. Rotation atomically replaces it, reuse deletes the entire family, and logout revokes the access JTI plus the cookie's matching family.

The SPA strips any response-body refresh token and stores only its short-lived access token/user metadata for reload continuity. Serve the production admin SPA and canonical API from the same site, normally the same origin behind Nginx, and keep browser fetch credentials enabled. A cross-site API host will not receive the strict host-only refresh cookie.

## Public widget boundary

The embedded `public/widget.js` loader calls `/widget/{publicId}/bootstrap` and `/widget/{publicId}/sessions` from the customer top page, allowing the server to validate the browser's real `Origin`. It mounts the iframe lazily and, after validating the iframe's ready message source/platform origin, passes bootstrap/session material with an explicit platform `targetOrigin`. No origin-forwarding header or query parameter is trusted. Reset requests repeat this parent-mediated session flow.

The first-party hosted demo uses the separate `/widget/{publicId}/hosted/bootstrap` and `/widget/{publicId}/hosted/sessions` routes. Hosted sessions require an active agent and rate limiting but intentionally do not apply a customer-domain allowlist. Both flows use the same bearer-protected widget message endpoint; embedded callers must never use the hosted routes.

## NVIDIA

`NVIDIA_API_KEY` is server-side only. The exact requested generation profile is available through environment defaults:

- `NVIDIA_MODEL=nvidia/nemotron-3-ultra-550b-a55b`
- `NVIDIA_TEMPERATURE=1`
- `NVIDIA_TOP_P=0.95`
- `NVIDIA_MAX_TOKENS=16384`
- `NVIDIA_ENABLE_THINKING=true`

The adapter consumes only visible `chunk.content`. Provider `reasoning_content` is ignored, never stored and never sent over SSE. For a lower-variance accuracy preset, change temperature through deployment configuration rather than source code.

Development can use deterministic feature-hash embeddings without an API key. Production startup validation requires `APP_AUTO_CREATE_SCHEMA=false`, `REQUIRE_NVIDIA=true` with `NVIDIA_API_KEY`, `ALLOW_DETERMINISTIC_EMBEDDINGS=false`, `RATE_LIMIT_FAIL_OPEN=false`, configured S3 credentials, a non-placeholder `JWT_SECRET` of at least 32 characters, explicit non-wildcard `APP_CORS_ORIGINS`, and non-placeholder database/Redis/RabbitMQ/object-store credentials. Build the SPA with `VITE_DEMO_MODE=false`; this is not an API setting.

Grounded chat loads up to eight eligible prior messages capped to the last 6,000 characters. With the default masking control, provider keys, emails, credential values, and valid payment-card numbers are redacted before persistence/model use. PostgreSQL retrieval filters by tenant, agent, approval status, and knowledge revision before combining 2,048-dimensional HNSW/cosine `halfvec` and full-text/GIN candidates with reciprocal-rank fusion and an absolute score threshold.

For S3-compatible uploads, `S3_ENDPOINT_URL` is the current process's private/download address and `S3_PUBLIC_ENDPOINT_URL` is the browser-visible address used when signing upload forms. Containers use `http://minio:9000` internally; a backend process running directly on the host uses `http://localhost:9000` for both settings. `/uploads/presign` requires SHA-256 of the exact file bytes. Its multipart policy signs the generated staging key, type, declared size/checksum metadata, and content-length range. Source creation pins and reads the staged version, verifies actual size/type/digest, conditionally copies the exact ETag/version to `knowledge/`, verifies the copy, and removes staging before committing the source/job.

## Processes

```bash
uv run celery -A northstar_api.workers.celery_app:celery_app worker --loglevel=INFO
uv run python -m northstar_api.workers.job_dispatcher
uv run python -m northstar_api.workers.outbox_publisher
uv run python -m northstar_api.workers.analytics_consumer
uv run python -m northstar_api.workers.object_cleaner
```

RabbitMQ delivery is at least once, so ingestion is checksum/version idempotent and terminal task status is persisted. The queue declares the durable `ingest.failed` route for broker-dead-lettered messages; an ordinarily acknowledged Celery failure is not guaranteed to land there. Kafka publication uses the PostgreSQL outbox. Consumers deduplicate by event ID; malformed envelopes/payloads are stored in `event_quarantine` with Kafka coordinates, payload SHA-256, and a redacted excerpt before the poison offset is committed. The object cleaner consumes `knowledge.source.deleted.v1`, accepts only an exact tenant-prefixed `knowledge/` key, and purges all versions/delete markers; storage failures remain uncommitted and retry. Deploy production database migrations with a schema-owner role and run HTTP/background processes with their documented separate restricted/service identities.
