# API

The FastAPI service exposes JSON REST endpoints under `/api/v1`, plus server-sent event (SSE) streams for chat. `/v1` is mounted as a non-browser compatibility alias, but `/api/v1` is canonical and the production refresh cookie is scoped to the configured canonical auth path. Local interactive documentation is available at `/docs` and the generated schema at `/openapi.json`; interactive documentation is disabled when `APP_ENV=production`.

Examples below use placeholder credentials and IDs.

## Conventions

- Base URL: `http://localhost:8100/api/v1` for direct local access.
- Unknown JSON request fields are rejected.
- Timestamps are ISO 8601 UTC values.
- Internal IDs are UUIDs. Published agents also have non-secret `publicId` values for widget bootstrap.
- Authenticated calls send `Authorization: Bearer <access-token>`.
- Clients may send `X-Request-ID`; the API returns the accepted/generated value in `X-Request-ID`.
- JSON fields use camelCase; query parameters use their documented snake_case names. Collection pagination uses `page` (from 1) and `page_size` (1–100).
- Empty successful deletes/logouts return `204 No Content`.

Errors have a stable envelope. Domain failures use an `http_<status>` code; validation failures use `validation_error` plus field details. Clients should branch on status/code, not exact English text.

```json
{
  "error": {
    "code": "http_404",
    "message": "Agent not found",
    "requestId": "request-correlation-id"
  }
}
```

## Authentication

### Log in

```http
POST /api/v1/auth/login
Content-Type: application/json

{
  "email": "admin@example.com",
  "password": "your-password"
}
```

Every successful login sets a host-only `northstar_refresh` cookie scoped to `${APP_API_PREFIX}/auth`, with `HttpOnly`, `SameSite=Strict`, the configured refresh TTL as `Max-Age`, and `Secure` in production. A production response contains `accessToken`, `expiresAt`, and a `user` object but omits the raw refresh token. Non-production/test responses retain `refreshToken` for CLI compatibility. The SPA deliberately strips any body refresh token before storing the access token/user session in `localStorage`; the Compose access-token lifetime is 15 minutes. Do not log or send either token to analytics.

Serve the production admin SPA and API from the same site—normally the same origin behind Nginx—and use the canonical `APP_API_PREFIX`. Browser requests use credentials mode `include`; a cross-site API deployment is incompatible with the `SameSite=Strict`, host-only refresh cookie. The access token still carries the active tenant and role and is sent as a bearer on protected API calls.

```bash
curl --fail-with-body http://localhost:8100/api/v1/auth/login \
  -H 'Content-Type: application/json' \
  --data '{"email":"admin@example.com","password":"your-password"}'
```

### Refresh and log out

```http
POST /api/v1/auth/refresh
Cookie: northstar_refresh=<http-only-cookie>
```

Non-production CLI clients may instead send `Content-Type: application/json` and `{"refreshToken":"<refresh-token>"}`. The request body is optional and takes precedence over the cookie when present.

```http
POST /api/v1/auth/logout
Authorization: Bearer <access-token>
Cookie: northstar_refresh=<http-only-cookie>
```

CLI clients may optionally put the matching `refreshToken` in the logout JSON body; browsers use the cookie. Refresh rotates the cookie and returns a new access token. Redis stores the single valid token ID for each refresh family; rotation is an atomic compare-and-replace. Presenting an old or unexpected token deletes the family, so the already-issued replacement is rejected too. Logout revokes the access-token ID until expiry, deletes a matching refresh family, and expires the cookie. Login and refresh fail closed with `503` when the production session store is unavailable.

## Authorization

| Capability | Analyst | Member | Owner/admin |
| --- | --- | --- | --- |
| Read agents, knowledge, conversations, leads, analytics, integrations | Yes | Yes | Yes |
| Send control-plane chat, reply to conversations, capture leads, and update workflow state | No | Yes | Yes |
| Create/update/delete agents and knowledge | No | No | Yes |
| Create/update curated facts | No | No | Yes |
| Change integration state | No | No | Yes |

Every authenticated route derives tenant scope from the token and verifies object ownership in the database.

## Endpoint summary

All routes in this table are relative to `/api/v1`.

| Method | Path | Authentication | Purpose |
| --- | --- | --- | --- |
| `POST` | `/auth/login` | Public | Exchange credentials for a session |
| `POST` | `/auth/refresh` | HttpOnly refresh cookie; optional non-production body token | Rotate the refresh family and access token |
| `POST` | `/auth/logout` | Access token + refresh cookie; optional body token | Revoke the access token and matching refresh family |
| `GET` | `/agents` | Access token | List tenant agents |
| `POST` | `/agents` | Owner/admin | Create an agent |
| `GET` | `/agents/{agentId}` | Access token | Read one agent |
| `PATCH` | `/agents/{agentId}` | Owner/admin | Patch configuration |
| `DELETE` | `/agents/{agentId}` | Owner/admin | Soft-delete an agent |
| `POST` | `/uploads/presign` | Owner/admin | Create a checksum-bound tenant-scoped upload form |
| `GET` | `/agents/{agentId}/knowledge` | Access token | List knowledge sources |
| `POST` | `/agents/{agentId}/knowledge` | Owner/admin | Queue a knowledge source |
| `DELETE` | `/knowledge/{sourceId}` | Owner/admin | Remove database derivatives and enqueue exact object purge |
| `GET` | `/agents/{agentId}/facts` | Access token | List curated Q&A facts |
| `POST` | `/agents/{agentId}/facts` | Owner/admin | Create/embed a fact |
| `PATCH` | `/facts/{factId}` | Owner/admin | Revise/moderate a fact |
| `GET` | `/jobs/{jobId}` | Access token | Inspect ingestion progress |
| `GET` | `/conversations` | Access token | Paginated conversation list |
| `GET` | `/conversations/{conversationId}` | Access token | Conversation with messages/citations |
| `PATCH` | `/conversations/{conversationId}` | Member/owner/admin | Update state, unread count, or visitor name |
| `POST` | `/conversations/{conversationId}/messages` | Member/owner/admin | Persist and publish a human teammate reply |
| `GET` | `/leads` | Access token | Paginated lead list |
| `POST` | `/leads` | Member/owner/admin | Capture a consent-aware lead |
| `PATCH` | `/leads/{leadId}` | Member/owner/admin | Update lead workflow status |
| `GET` | `/analytics/summary` | Access token | Last-30-day summary and trends |
| `GET` | `/integrations` | Access token | Integration catalog and state |
| `PATCH` | `/integrations/{integrationId}` | Owner/admin | Connect/disconnect an available integration |
| `POST` | `/chat/stream` | Member/owner/admin | Stream authenticated control-plane chat |
| `POST` | `/messages/{messageId}/feedback` | Member/owner/admin | Record positive/negative feedback |
| `POST` | `/chat/completions` | Member/owner/admin | OpenAI-compatible completion shape using an agent as `model` |
| `GET` | `/widget/{publicId}/bootstrap` | Allowed Origin | Fetch safe public widget configuration |
| `POST` | `/widget/{publicId}/sessions` | Allowed Origin | Create a visitor conversation/session token |
| `GET` | `/widget/{publicId}/hosted/bootstrap` | Public, first-party hosted page | Fetch hosted-page widget configuration |
| `POST` | `/widget/{publicId}/hosted/sessions` | Public, first-party hosted page | Create a hosted-page conversation/session token |
| `POST` | `/widget/sessions/{conversationId}/messages` | Matching widget bearer token | Stream a widget reply |

## Agents

Create an agent:

```json
{
  "name": "Customer Support",
  "description": "Answers product and policy questions",
  "template": "support"
}
```

`PATCH /agents/{agentId}` is a partial update. Nested `appearance`, `model`, and `security` objects are validated as complete profiles when provided. The deployment only accepts its configured NVIDIA model.

```json
{
  "instructions": "Answer only from approved evidence. If evidence is missing, say so.",
  "tone": "friendly",
  "status": "active",
  "model": {
    "provider": "nvidia",
    "model": "nvidia/nemotron-3-ultra-550b-a55b",
    "temperature": 1,
    "topP": 0.95,
    "maxTokens": 16384,
    "enableThinking": true,
    "citationMode": "when-available"
  }
}
```

Valid statuses are `active`, `draft`, `training`, and `error`. Valid tones are `professional`, `friendly`, `concise`, `empathetic`, and `playful`.

## Knowledge and facts

Knowledge kinds are `text`, `url`, `sitemap`, and `file`:

```json
{
  "name": "Refund policy",
  "kind": "text",
  "content": "Approved policy content goes here."
}
```

URL/sitemap sources require `url`. File sources require an object key produced by the upload/storage flow (or accepted inline content in controlled development). The create response starts in `processing`/`Queued`; poll the associated job when a job ID is available in the workflow, or refresh the source list until it reaches `ready` or `failed`.

For a file, first request a short-lived, tenant-scoped upload URL:

```http
POST /api/v1/uploads/presign
Authorization: Bearer <access-token>
Content-Type: application/json

{
  "filename": "handbook.pdf",
  "contentType": "application/pdf",
  "sizeBytes": 482931,
  "checksumSha256": "<required-lowercase-64-character-hex-digest>"
}
```

Compute SHA-256 over the exact file bytes before requesting the upload. The response contains `method: "POST"`, `url`, `objectKey`, an expiry, and a `fields` map. Submit a `multipart/form-data` POST to `url`, copying every returned field exactly and appending the same bytes as the file part last, with the requested media type and declared byte length. Do not turn the fields into HTTP headers or omit fields that appear unfamiliar; they carry the signature and upload policy. Then create the source using `objectKey`:

```json
{
  "name": "Employee handbook",
  "kind": "file",
  "objectKey": "<returned-object-key>"
}
```

The form policy binds the generated staging key, content type, declared size metadata, SHA-256 metadata, and a byte range capped at the declared size. Source creation does not trust those fields alone: the API pins the staged object version when available, verifies the actual size, type, and SHA-256 from the bytes, conditionally copies that exact ETag/version to a generated `knowledge/` key, verifies the copy size, and only then deletes staging. The validated snapshot—not a mutable client key—is committed with source/job metadata. Clients must never invent or reuse another tenant's key. Supported file media types are PDF, DOCX, plain text, and Markdown. The default request/object limit is 25 MiB and the default presign lifetime is 15 minutes.

Deleting a source removes its database-backed chunks/facts and commits `knowledge.source.deleted.v1`. The object-cleaner consumer validates the exact tenant-prefixed key, deduplicates the event, and asynchronously purges every version and delete marker. A successful `204` therefore means database deletion is committed; monitor the cleanup consumer for completion of physical object erasure.

Curated facts are directly embedded Q&A records:

```json
{
  "question": "How long is the trial?",
  "answer": "The trial lasts seven days.",
  "priority": 10,
  "status": "approved"
}
```

Fact status is `pending`, `approved`, `rejected`, or `archived`. `GET /agents/{agentId}/facts?fact_status=approved` filters by status.

## Conversations and leads

```http
GET /api/v1/conversations?page=1&page_size=50
Authorization: Bearer <access-token>
```

Conversation state is `open`, `resolved`, or `escalated`. A patch may contain `state`, `unread`, and/or `visitorName`. Conversation responses include ordered messages and persisted citation metadata.

Lead creation is explicit about consent:

```json
{
  "agentId": "00000000-0000-0000-0000-000000000000",
  "conversationId": null,
  "name": "Example Visitor",
  "email": "visitor@example.com",
  "phone": null,
  "consent": true,
  "fields": {"companySize": "11-50"}
}
```

## Streaming chat

Authenticated control-plane chat:

```http
POST /api/v1/chat/stream
Authorization: Bearer <access-token>
Accept: text/event-stream
Content-Type: application/json

{
  "agentId": "<agent-uuid-or-public-id>",
  "message": "What is the refund policy?",
  "conversationId": null,
  "visitorId": "visitor-opaque-id",
  "idempotencyKey": "client-generated-unique-value"
}
```

Each SSE frame contains one `data:` value:

```text
data: {"type":"start","conversationId":"...","messageId":"..."}

data: {"type":"token","content":"The "}

data: {"type":"citation","title":"Refund policy","url":"https://example.com/policy"}

data: {"type":"done","conversationId":"..."}

data: [DONE]
```

Clients should tolerate future event fields, append only `token.content`, attach citations to the completed assistant message, and stop on either the typed `done` event or `[DONE]`. Nginx disables proxy buffering for this path.

An `idempotencyKey` prevents a retry from creating a second user/assistant pair. Reusing a key while the first request is still processing returns `409`; after completion it returns the persisted answer.

When no evidence passes retrieval thresholds, the service abstains rather than asking the model to guess.

Before generation, PostgreSQL retrieval filters candidates by tenant, agent, approval state, and the conversation's pinned knowledge revision. It combines 2,048-dimensional `halfvec` cosine/HNSW ranking with full-text/GIN ranking through reciprocal-rank fusion, then applies the absolute score threshold and context cap. Redis is not used as a retrieval cache.

The coordinator supplies at most eight eligible prior messages and the last 6,000 history characters. A short referential follow-up can reuse the most recent complete user question for retrieval while the current question and bounded history remain in the generation prompt. With `maskSensitiveData` enabled, provider keys, email addresses, credential assignments, and Luhn-valid payment-card numbers are redacted before message persistence and model use.

## Embedded widget flow

The public `widget.js` loader, not the platform iframe, owns origin-authorized bootstrap and session creation:

1. On the customer top page, the loader sends `GET /widget/{publicId}/bootstrap` directly to the platform. The browser supplies its authoritative `Origin`; the loader sends no credentials and no forwarded-origin query or custom header.
2. The loader mounts the launcher without an iframe `src`, so page view alone creates neither an iframe session nor a database conversation.
3. On first open, the loader assigns `/widget/{publicId}` to the iframe. The iframe sends a secret-free `northstar:ready` message.
4. The loader accepts that message only from the iframe window and platform origin, then sends `POST /widget/{publicId}/sessions` from the customer top page. It transfers the bootstrap and short-lived session to the iframe with `postMessage(..., platformOrigin)`; bearer material is never sent to `*`.
5. The iframe posts messages to `/widget/sessions/{conversationId}/messages` with `Authorization: Bearer <sessionToken>`. The API verifies the token's tenant, agent, conversation, and captured deployment origin, and rechecks that origin against the agent's current allowlist.
6. “Start a new conversation” uses a nonce-correlated request to the parent loader. The loader repeats the validated session call and returns the new secret only to the platform-origin iframe.

```json
{
  "visitorId": "opaque-browser-id",
  "visitorName": "Example Visitor",
  "visitorEmail": "visitor@example.com",
  "pageUrl": "https://customer.example/help"
}
```

The public agent must be active and the top-page origin host must match its normalized `allowedDomains`. Wildcard subdomains use an explicit `*.example.com` entry. Missing or malformed Origin is accepted only in development/test.

## Hosted widget flow

The first-party `/demo/{publicId}` page uses `GET /widget/{publicId}/hosted/bootstrap` and lazily creates conversations with `POST /widget/{publicId}/hosted/sessions`. These routes still require an active published agent and apply session rate limits, but they do not apply the customer-domain allowlist because the page is hosted by Northstar itself. The returned hosted token uses the same bearer-protected message endpoint. Embedded installations must not substitute the hosted routes, because doing so would bypass the customer-origin deployment check.

## OpenAI-compatible endpoint

`POST /chat/completions` supports the common non-streaming and streaming chat-completion response shapes. In this API, `model` identifies the configured Northstar agent (UUID or `publicId`), not an arbitrary provider model. The server chooses the allowed NVIDIA model from agent/deployment configuration.

```json
{
  "model": "<agent-id-or-public-id>",
  "messages": [
    {"role": "user", "content": "Summarize the approved cancellation policy."}
  ],
  "stream": true,
  "user": "opaque-end-user-id"
}
```

## Feedback and rate limits

Feedback accepts `{"value":1}` or `{"value":-1,"reason":"optional explanation"}`. Sending feedback again for the same message updates the existing value.

Rate-limited chat returns `429` with `Retry-After`. Clients should honor it and use jittered retry rather than immediately opening another connection.

## Service endpoints

The process-level endpoints are outside `/api/v1`:

- `GET /health/live` — process liveness.
- `GET /health/ready` — readiness for traffic.
- `GET /metrics` — Prometheus metrics.

Do not expose metrics or interactive API documentation publicly without ingress authentication/network policy.
