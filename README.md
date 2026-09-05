# Northstar AI Platform

Northstar is a multi-tenant AI support platform for creating grounded assistants, managing knowledge, reviewing conversations, and deploying a configurable website chat experience. The repository contains a React control plane and a FastAPI service backed by PostgreSQL/pgvector, Redis, RabbitMQ, Kafka, and S3-compatible object storage.

The default Docker Compose topology is a production-shaped local environment: it exercises migrations, background workers, transactional event delivery, analytics consumption, persistent volumes, health gates, and the same reverse-proxy path used by the browser. It is intentionally single-node and is not a substitute for a highly available production deployment.

## Security notice

An NVIDIA credential and a third-party account password were exposed in the original project request. Treat both as compromised: revoke/rotate them with their respective providers before running this application. Neither exposed value is stored in this repository.

Never commit `.env`, credentials, exported customer data, or browser-audit artifacts. For production, inject secrets from a secrets manager and set the hardened flags described in [Security](docs/security.md#production-baseline).

## Features

- Agent creation and management with instructions, identity, tone, model, citation, rate-limit, retention, and widget controls.
- Knowledge ingestion for files, URLs, sitemaps, and plain text, with `halfvec` dense search, PostgreSQL full-text search, reciprocal-rank fusion, and source citations.
- Grounded-before-stream chat over server-sent events, bounded conversation history, sensitive-data redaction, status changes, sentiment, and escalation workflows.
- A customizable website widget with a lazy, origin-checked parent/iframe handshake, a separate first-party hosted page, and launcher, color, typography, greeting, suggested-question, placement, privacy, and branding settings.
- Operational dashboard, conversation analytics, knowledge health, and integration management.
- Meta WhatsApp Cloud API integration with Embedded Signup v4, existing/new phone-number selection, encrypted customer tokens, signed/idempotent webhooks, and queued AI and human replies.
- Project configuration for Meta's official Social Technologies MCP developer workflows, including app audits, webhook diagnostics, App Review, compliance, API health, documentation search, and token diagnosis. See [the MCP guide](docs/meta-devtools-mcp.md).
- Tenant-scoped authentication and authorization with short-lived access tokens, production HttpOnly refresh cookies, and Redis-backed one-time refresh-token families that revoke on reuse.
- Durable background work through RabbitMQ/Celery, replayable domain events through Kafka, and Redis-backed coordination.
- Transactional outbox delivery so database commits and published analytics events do not silently diverge.
- Structured logs, Prometheus metrics, health/readiness probes, CI checks, and container health gates.

## Quick start

### Windows: one click

Double-click `start.bat` in the repository root. It verifies Docker Desktop is running, creates `.env` if it is missing, stops any previous Northstar containers to release the host ports, refuses to continue if a required port is still occupied (naming the container that holds it), builds and starts every service, waits for the API and web health checks, prints the seeded sign-in credentials, and opens Swagger UI and the web app in your browser.

Sign in with the `SEED_ADMIN_EMAIL` / `SEED_ADMIN_PASSWORD` values from `.env` (`admin@example.com` by default). To exercise a protected route in Swagger, call `POST /api/v1/auth/login`, copy `accessToken` from the response, then click **Authorize** and paste it.

Shut everything down with `docker compose down`.

Prerequisites:

- Docker Engine with Docker Compose v2
- At least 8 GB of memory available to Docker
- Optional for host development: Node.js 22.12+, Python 3.12+, and `uv`

On macOS/Linux, the following creates `.env` from the documented placeholders and starts the full stack:

```bash
make up
make smoke
```

On PowerShell:

```powershell
Copy-Item .env.example .env
docker compose up --build --detach
./infra/smoke.ps1
```

Before sharing the environment or connecting real data, edit `.env` and replace every `replace-...`/`change-me-...` value. Add a newly rotated `NVIDIA_API_KEY` to enable live model responses.

Local endpoints:

| Service | URL | Notes |
| --- | --- | --- |
| Web application | http://localhost:3100 | Nginx serves the SPA and proxies `/api/*` |
| API | http://localhost:8100 | Direct API access |
| OpenAPI UI | http://localhost:8100/docs | Interactive schema in non-production environments |
| Metrics | http://localhost:8100/metrics | Prometheus exposition |
| RabbitMQ management | http://localhost:15673 | Uses the local `.env` RabbitMQ credentials |
| MinIO console | http://localhost:9011 | Uses the local `.env` MinIO credentials |

All host-published ports bind to `127.0.0.1` by default. Change the bind variables only when the host firewall and authentication posture are understood.

File uploads use `S3_PUBLIC_ENDPOINT_URL=http://localhost:9010` for the presigned form returned to the browser, while API and worker object access uses `S3_ENDPOINT_URL=http://minio:9000` on the private Compose network. The client must compute SHA-256 over the exact bytes before requesting a form. The signed multipart fields bind the media type, declared size, checksum, and maximum byte range; when the source is created, immutable-snapshot promotion pins the staged object version/ETag, reads and verifies its bytes, conditionally copies that exact snapshot into the durable `knowledge/` prefix, verifies the copy, and removes staging. Community MinIO applies `MINIO_API_CORS_ALLOW_ORIGIN` cluster-wide; the local default explicitly allows the documented `localhost` and `127.0.0.1` web/development origins on ports 3100 and 8100. If you change the browser origin, update that variable as well as `APP_CORS_ORIGINS`. Production must use exact deployed origins and point both endpoint settings at appropriate internal/public routes for the same private bucket (often one split-horizon object-store hostname). The current browser upload flow requires multipart `POST`, not presigned `PUT`; add `GET`/`HEAD` to bucket CORS only if a browser download flow needs them. Local MinIO expires every version and delete marker for abandoned `staging/` uploads after one day.

The web image bakes a restrictive admin Content Security Policy at build time. Set `WEB_CSP_EXTRA_CONNECT_SRC` to a space-separated list of exact HTTP(S) origins and include `S3_PUBLIC_ENDPOINT_URL` exactly (for example, set both to `https://objects.example.com`); the image build fails if they drift or if a value contains a path, credentials, query, fragment, or invalid port. Rebuild the web image whenever either value changes. The policy already includes the minimum Meta SDK/login endpoints. The public `/widget/` route overrides it with a separate self-only policy.

## NVIDIA model configuration

The requested configuration is supported directly and is the repository default:

```dotenv
NVIDIA_MODEL=nvidia/nemotron-3-ultra-550b-a55b
NVIDIA_TEMPERATURE=1
NVIDIA_TOP_P=0.95
NVIDIA_MAX_TOKENS=16384
NVIDIA_ENABLE_THINKING=true
```

Thinking content is handled server-side and is never exposed as hidden chain-of-thought. The service first consumes the provider response privately, persists the complete grounded answer and citations, and then replays that verified result over SSE. Only user-facing answer tokens and explicit source metadata cross the public stream.

For lower-variance factual support answers, an **Accuracy preset** can use `NVIDIA_TEMPERATURE=0.2` while retaining retrieval, citations, and thinking. Lower temperature improves repeatability; it does not make ungrounded content correct. Accuracy comes primarily from clean source material, tenant-safe retrieval, citation requirements, abstention behavior, and evaluated prompts.

Useful related variables:

- `RETRIEVAL_CANDIDATE_LIMIT` controls the initial vector candidates.
- `RETRIEVAL_CONTEXT_LIMIT` caps sources added to model context.
- `RETRIEVAL_MIN_SCORE` rejects weak matches.
- `REQUIRE_NVIDIA=true` disables the grounded text fallback on provider failure; production configuration also rejects a missing provider key.
- `ALLOW_DETERMINISTIC_EMBEDDINGS=false` disables the local embedding fallback required to be off in production.

## WhatsApp Business connection

The Integrations page now launches Meta's official Embedded Signup v4 flow. A workspace owner signs in to Facebook, chooses an existing WhatsApp Cloud API number or adds and verifies a new number, selects an active Northstar agent, and supplies the required six-digit two-step-verification PIN. Northstar immediately exchanges Meta's short-lived authorization code on the server, validates the selected WABA and phone number, registers the number, subscribes the WABA webhook, encrypts the customer token, and begins routing messages through RabbitMQ workers.

Live connection requires a Meta Business app, a Facebook Login for Business Embedded Signup configuration, the WhatsApp product, Advanced Access to the required WhatsApp permissions, and a publicly reachable HTTPS callback at:

```text
https://YOUR_DOMAIN/api/v1/webhooks/whatsapp
```

Set the `META_*` variables documented in `.env.example`; never put the App Secret, webhook verification token, token-encryption key, customer token, or registration PIN in a `VITE_*` variable. The local HTTP Compose URL is suitable for development but cannot be registered as Meta's production webhook.

The standard flow supports numbers already present in a WABA and newly verified Cloud API numbers. A number currently used by the WhatsApp Business mobile app requires Meta's separate Coexistence onboarding product, which this standard flow intentionally does not claim to support. SMS/voice verification, Meta consent, business review, and account approval remain user/Meta-controlled steps and cannot be bypassed.

Follow the complete [Meta WhatsApp setup and operations guide](docs/whatsapp.md) before connecting a real number.

## Demo and live behavior

The frontend has two deliberately distinct modes:

- Docker images build with `VITE_DEMO_MODE=false`. The UI uses the real API and never silently replaces an API failure with browser-local data.
- Frontend-only development may use `VITE_DEMO_MODE=true`. When the API is unreachable or has a server failure, seeded browser data and simulated streaming keep UI work usable. Authentication/validation errors are still surfaced. Demo changes live only in browser storage and are not durable or shared.

Production browser login keeps only the short-lived access-token/user session in `localStorage`; the raw refresh token is held in a host-only `HttpOnly`, `SameSite=Strict`, `Secure` cookie scoped to `${APP_API_PREFIX}/auth`. Serve the admin SPA and canonical API from the same site, normally the same origin behind Nginx. Non-production/test API responses keep refresh-token bodies only for CLI compatibility, and the SPA strips them before persistence.

The environment seeder is for disposable local development: `SEED_ADMIN_EMAIL` and `SEED_ADMIN_PASSWORD` must be set together, and `SEED_DEMO_AGENT=true` adds the sample agent only while creating that first owner. Provision production identities through a controlled administrative workflow instead. In production, leave both seed credentials unset and keep `SEED_DEMO_AGENT=false` and `ALLOW_PRODUCTION_SEED=false`; the API rejects production seed credentials unless the explicit override is enabled and the password is at least 12 characters without `change-me`.

## Stack and message semantics

| Component | Responsibility | Durability contract |
| --- | --- | --- |
| PostgreSQL + pgvector | Tenant data, configuration, conversations, knowledge metadata/`halfvec` vectors, outbox, projections, event quarantine | System of record; backed up and migrated |
| MinIO | Uploaded knowledge source objects | Private, versioned local bucket |
| Redis | Rate-limit counters, access-token revocations, and refresh-family rotation state | Persistence/no-eviction required for live security TTLs; never business records |
| RabbitMQ + Celery | Document ingestion plus WhatsApp inbound AI work and outbound human delivery | At-least-once; handlers are idempotent and WhatsApp work is serialized per customer thread |
| Job dispatcher | Claims durable queued ingestion/WhatsApp rows and submits them to RabbitMQ | Retries broker publication without losing the database-backed job or message receipt |
| Kafka (KRaft) | Ordered, replayable domain/analytics event stream | At-least-once; consumers deduplicate by event ID |
| Outbox relay | Publishes committed database events to Kafka | Retries unpublished rows; duplicates are possible |
| Analytics consumer | Builds query-efficient analytics projections | Replayable and idempotent |
| Object cleaner | Consumes committed knowledge deletions and purges every stored version and delete marker for the exact object key | Replayable, tenant-prefix validated, and idempotent |

RabbitMQ and Kafka are not interchangeable. RabbitMQ carries work that should be completed by one worker; Kafka retains facts that multiple independent consumers may replay. Redis holds bounded rate-limit and session-security state—not retrieval results and not a replacement for either broker. An unavailable production Redis fails closed. Data loss/eviction invalidates refresh families and can erase live rate windows or access-token revocations, so persistence and a no-eviction policy for security keys are required.

See [Architecture](docs/architecture.md) and [Operations](docs/operations.md) for the complete flows and failure semantics.

## Development

Install all host dependencies:

```bash
make bootstrap
```

Run the frontend and API in separate terminals:

```bash
npm run dev
npm run api:dev
```

Quality checks:

```bash
make lint
make typecheck
make test
make build
docker compose config --quiet
```

Common Compose commands:

```bash
make ps                 # service and health status
make logs               # follow bounded container logs
make migrate            # run migrations as a one-shot container
make down               # stop containers; preserve data
make clean              # stop and remove orphan containers; preserve data
make reset-data          # destructive: also remove named data volumes
```

## Repository layout

```text
apps/web/              React + TypeScript administration UI and widget preview
services/api/          FastAPI API, persistence, model/RAG services, and workers
infra/                 Database initialization and smoke checks
docs/                  Architecture, API, security, and operational guidance
.github/                CI and dependency-update automation
compose.yaml            Complete local service topology
```

## Documentation

- [Architecture and event flows](docs/architecture.md)
- [HTTP and streaming API](docs/api.md)
- [Security model and production checklist](docs/security.md)
- [Operations and recovery](docs/operations.md)
- [Meta WhatsApp setup and operations](docs/whatsapp.md)

## Production expectations

Before production use, replace the single-node data services with durable, monitored deployments; require TLS and authenticated broker connections; use at least three Kafka brokers/controllers; configure PostgreSQL point-in-time recovery; configure object lifecycle and backup policies; run multiple API/worker replicas; disable local fallbacks and environment seeding; and serve the admin web/API from the same site behind a trusted TLS ingress. Apply the exact fail-fast settings in the [production security baseline](docs/security.md#production-baseline).

Pin images by digest in the deployment environment and promote tested artifacts rather than rebuilding from mutable branches. CI validates source and container builds, but deployment approval, database backup verification, model evaluation, and credential rotation remain explicit release gates.
