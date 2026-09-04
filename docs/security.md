# Security

## Immediate credential action

The model credential and third-party account password included in the original request must be considered compromised. Revoke/rotate both, review their usage histories where available, and issue a new restricted model credential. Store replacements only in a password manager, deployment secret manager, or an uncommitted local `.env` as appropriate; never place them in source, frontend build arguments, images, logs, issue text, screenshots, or chat transcripts.

CI runs secret scanning, but scanning does not make an exposed credential safe. Rotation is the remediation.

## Trust boundaries

- Browsers, widget visitors, uploaded files, crawled web pages, and retrieved knowledge text are untrusted.
- The API is the authorization boundary. The frontend is not trusted to enforce roles or tenant ownership.
- Workers have broader data access than browsers and should run with separate, least-privilege identities.
- RabbitMQ, Kafka, Redis, PostgreSQL, and object storage use private application networks. Compose also gives them loopback-only published development ports through `local-access`, plus a worker-only outbound bridge for crawls and provider calls; remove the local-access path and enforce destination-aware worker egress policy in production.
- Model providers receive only the minimum prompt context required for the active request.

## Secrets

Production secrets include JWT signing material, database/broker/object-store credentials, model-provider keys, OAuth client secrets, webhook signing keys, and encryption keys.

Required controls:

1. Inject secrets at runtime from a managed secret store; do not bake them into an image.
2. Use separate values per environment and service identity.
3. Restrict provider keys by project, allowed API, and spend limit when supported.
4. Rotate on a schedule and immediately after disclosure, employee departure, or suspected misuse.
5. Keep values out of exception messages and structured-log context; log only a stable credential identifier when needed.
6. Prefer asymmetric signing or a managed key service when multiple services validate tokens.

`.env.example` contains placeholders only. The local Compose defaults are intentionally recognizable and are not valid production credentials.

## Authentication and tenant isolation

- Passwords are hashed with a memory-hard password hash; plaintext passwords are never logged or returned.
- Login responses are rate limited and do not reveal whether an address exists.
- Access tokens have a short lifetime and validate issuer, audience, algorithm, expiry, and token type.
- Production refresh tokens are available only in the host-only `northstar_refresh` cookie: `HttpOnly`, `SameSite=Strict`, `Secure`, refresh-TTL `Max-Age`, and path `${APP_API_PREFIX}/auth`. Serve the admin SPA and canonical API from the same site (normally the same origin behind Nginx); do not weaken the cookie to support a cross-site control plane.
- The SPA persists the short-lived access token and user metadata in `localStorage` for reload continuity and explicitly strips any response-body refresh token. Treat XSS prevention as a bearer-token control: keep CSP restrictive, avoid unsafe HTML, and minimize third-party script access to the admin origin.
- The API stores the single valid token ID for each refresh family in Redis and atomically replaces it on rotation. Reuse or any unexpected token ID deletes the entire family, invalidating the current replacement as well. Logout revokes the access-token ID and deletes the matching refresh family obtained from the cookie or optional compatibility body; production fails closed when this session store is unavailable.
- Every database access path includes tenant scope derived from authenticated server context.
- Create/update payloads do not accept authoritative tenant or owner IDs from the browser.
- Role checks are enforced on mutations and sensitive reads; tests cover cross-tenant object IDs.
- Database identities are separated: migrations use the schema owner, the HTTP API uses the restricted `northstar_app` login under tenant RLS, and background scanners use a non-owner `northstar_service` login with `BYPASSRLS`. Never use the service identity for HTTP request handling.

Non-production/test APIs retain refresh tokens in response/request bodies for CLI compatibility; never enable that behavior as a production browser workaround. The strict refresh cookie mitigates cross-site refresh/logout requests, while access-token API calls remain bearer-authenticated.

## Widget and API exposure

- Use an explicit origin allowlist. Wildcard CORS with credentials is forbidden.
- Validate the configured allowed domains for each deployed widget on the server. The embedded loader performs bootstrap/session requests from the customer top page so the browser supplies the authoritative `Origin`; do not accept a caller-provided origin query or `X-Widget-Origin` header.
- Keep bootstrap/session bearer material in the parent-to-iframe direction only. The loader validates the iframe window and platform origin before creating a session, then uses an explicit platform `targetOrigin`; the iframe accepts initialization only from `window.parent`. The secret-free ready/reset request may use `*`, but secret-bearing replies may not.
- Keep embedded `/widget/{publicId}/bootstrap` and `/sessions` separate from the first-party `/hosted/bootstrap` and `/hosted/sessions` routes. Hosted routes intentionally bypass the customer-domain allowlist and must never be used by third-party installs.
- Put public chat behind an edge rate limiter/bot control in addition to the Redis application limiter.
- Apply request body and message-size limits before parsing or model invocation.
- Sanitize rendered Markdown/HTML and block dangerous URL schemes.
- Use a restrictive Content Security Policy. The web image builds the admin policy with the required Meta endpoints and validated `WEB_CSP_EXTRA_CONNECT_SRC` origins; the build fails unless that list contains `S3_PUBLIC_ENDPOINT_URL` exactly, preventing a deployed policy from silently blocking direct browser uploads. Rebuild the image when either value changes. The local Nginx widget route must permit external framing to function and therefore overrides the admin policy with self-only resources and `frame-ancestors *`; per-agent API origin checks gate bootstrap and sessions. For stronger production enforcement, have the edge serve the widget shell with a per-agent `frame-ancestors` allowlist or require a signed installation token. The admin SPA retains `X-Frame-Options: SAMEORIGIN`.
- Verify webhook signatures over the raw body and reject stale timestamps/replayed delivery IDs.

## AI and retrieval safety

Retrieved documents and user messages can contain prompt injection. They are data, not trusted instructions.

- Keep system policy, application directives, retrieved excerpts, tool results, and user input in separate structured prompt sections.
- Instruct the model to ignore commands embedded in evidence and to abstain when evidence is absent or conflicting.
- Filter retrieval by tenant and agent before similarity ranking; never filter only after vector search.
- Production retrieval combines tenant/agent/revision-filtered `halfvec` HNSW cosine candidates with PostgreSQL full-text candidates through reciprocal-rank fusion, then applies an absolute relevance threshold. Redis is not a retrieval cache.
- Bound conversation context before model use. The implementation includes at most eight eligible prior messages and the last 6,000 characters; short referential follow-ups reuse the previous complete user question for retrieval.
- When `maskSensitiveData` is enabled (the default), mask provider keys, email addresses, credential assignments, and Luhn-valid payment-card numbers before persistence, retrieval-query construction, and model input.
- Return citations from persisted source/chunk metadata rather than accepting model-invented citations.
- Do not expose provider `reasoning_content`, internal prompts, connection credentials, or hidden tool output.
- Permit tools through a server-side allowlist with schema validation, least-privilege credentials, timeouts, and explicit side-effect authorization.
- Maintain adversarial evaluations for cross-tenant extraction, prompt leakage, fabricated citations, PII reflection, and unsafe URLs.

## Upload and crawler controls

- The implementation enforces upload bytes, extracted characters, PDF pages, DOCX member/expanded size, sitemap URLs, and chunk count. Add worker hard/soft processing deadlines at deployment; no application task-time limit is configured in this repository.
- Require the client SHA-256 for the exact bytes before issuing a presigned form. Bind the staging key, media type, declared size/checksum metadata, and content-length range in the multipart policy; returned form fields are not HTTP headers.
- On source creation, pin the staged object version when available, verify actual size/type/SHA-256 from the bytes, conditionally copy the exact ETag/version to the durable `knowledge/` key, verify the copy, and delete staging. Never ingest directly from an unpromoted or caller-invented key.
- Validate supported container/file structure and extraction limits. This repository does not include a malware scanning engine; deploy one before accepting untrusted production uploads, and quarantine or reject failed/unknown files before extraction.
- Store objects privately under generated tenant-prefixed keys; never trust a user-supplied storage key.
- Restrict object-store CORS to exact deployed web origins and methods. The current browser upload uses multipart `POST`, not presigned `PUT`; allow `GET`/`HEAD` only if a deployed browser download path needs them. Community MinIO's Compose setting is cluster-wide through `MINIO_API_CORS_ALLOW_ORIGIN`; use bucket-scoped CORS on production object stores that support it.
- Strip active document content and do not execute macros, scripts, or embedded binaries.
- For URL ingestion, allow only `http`/`https`, resolve DNS safely, reject loopback/private/link-local/metadata destinations, cap redirects, revalidate every redirect target, and prevent DNS rebinding.
- Apply egress policy so crawler workers cannot reach the control plane or cloud metadata services.

## Messaging safety

- Authenticate and encrypt all broker connections outside the local machine.
- Give producers/consumers topic- or vhost-scoped identities rather than administrator credentials.
- Validate message schemas and maximum size before processing.
- Include event IDs and idempotency keys; duplicates are expected under at-least-once delivery.
- Bound retries and route poison messages to a restricted dead-letter destination.
- Kafka consumers validate envelopes and payloads. Malformed records are persisted in `event_quarantine` with Kafka coordinates, a SHA-256, and a redacted bounded excerpt before their offsets are committed; operational failures remain retryable.
- The knowledge object-cleanup consumer accepts only `knowledge.source.deleted.v1`, validates the exact `knowledge/{tenantId}/` prefix, deduplicates by event ID, and permanently purges all versions/delete markers for that one key. Broad prefix deletion is forbidden.
- Do not place model keys, access tokens, full documents, or unnecessary PII in event payloads.

## Logging, privacy, and retention

Structured logs use request/correlation IDs and service names. They redact authorization headers, cookies, credentials, raw prompts, document content, and visitor PII by default. Logs and metrics must not use tenant IDs, user IDs, or conversation IDs as unbounded metric labels.

Agent configuration records a retention period, but this repository does not include a scheduled age-based retention worker. Explicit source deletion cascades indexed database derivatives and drives an idempotent, version-aware object purge through Kafka. Before production, implement and verify age-based retention across PostgreSQL, objects, Kafka, quarantine records, and backups—not only in the UI. Monitor cleanup lag and audit access to exports and administrative changes.

## Container and network baseline

- Run as a non-root user with a read-only filesystem where the runtime permits it.
- Set `no-new-privileges`, drop unused Linux capabilities, and use small multi-stage images.
- Keep data/event networks private and expose only the ingress service publicly.
- Scan OS and language dependencies, generate an SBOM, pin promoted images by digest, and sign release artifacts.
- Separate migration permissions from ordinary runtime permissions when deploying beyond Compose.

## Production baseline

Set at minimum:

```dotenv
APP_ENV=production
APP_AUTO_CREATE_SCHEMA=false
REQUIRE_NVIDIA=true
ALLOW_DETERMINISTIC_EMBEDDINGS=false
RATE_LIMIT_FAIL_OPEN=false
SEED_DEMO_AGENT=false
ALLOW_PRODUCTION_SEED=false
VITE_DEMO_MODE=false
```

Leave `SEED_ADMIN_EMAIL` and `SEED_ADMIN_PASSWORD` unset in production and provision identities through a controlled administrative path. `ALLOW_PRODUCTION_SEED` is an explicit break-glass gate, not a normal bootstrap mechanism; when set, validation still requires a password of at least 12 characters that does not contain `change-me`. Keep it disabled after the one intended startup.

Production configuration validation also requires a non-placeholder `JWT_SECRET` of at least 32 characters, `REQUIRE_NVIDIA=true` with `NVIDIA_API_KEY`, configured S3 credentials, explicit `APP_CORS_ORIGINS` without `*`, and database/Redis/RabbitMQ/object-store credentials without documented placeholder markers. `VITE_DEMO_MODE=false` is a frontend build setting rather than an API setting, but is required so production UI failures cannot fall back to browser-local demo data. These startup checks do not enforce TLS, broker topology, malware scanning, backups, or ingress policy; verify those controls separately.

Before launch, verify:

- Cross-tenant authorization and object-reference tests pass.
- Restore tests prove PostgreSQL and object backups are usable.
- Kafka/RabbitMQ authentication, TLS, retention, retry, and dead-letter policies are applied.
- Redis authentication/TLS, persistence, backups where required, and a no-eviction policy preserve live revocation/rate-limit keys.
- CSP, CORS, trusted proxy, cookie, and CSRF settings match the actual ingress.
- An external upload malware-scanning/quarantine stage is deployed; the included crawler SSRF protections and egress policy are active.
- Provider spend/rate limits and cost alerts are configured.
- Secret scanning includes full Git history and build artifacts.
- Incident owners and credential-rotation procedures are documented and exercised.
