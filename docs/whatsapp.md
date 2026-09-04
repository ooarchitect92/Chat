# Meta WhatsApp setup and operations

Northstar connects to the official Meta WhatsApp Business Platform Cloud API through Embedded Signup v4. The supported result is one Cloud API phone number per Northstar workspace, bound to one active agent. The Meta picker can select a Cloud API number already attached to the chosen WhatsApp Business Account (WABA), or it can add and verify a new number.

This is not a WhatsApp Web QR-code integration and it never asks Northstar for a Facebook password. Meta owns the login, consent, business selection, number selection, and SMS/voice verification screens.

## Supported number paths

| Number state | Supported here | What happens |
| --- | --- | --- |
| Already present in the selected WABA as a Cloud API number | Yes | Select it in Meta and enter its existing six-digit registration PIN. |
| New business number | Yes | Add it in Meta, complete SMS/voice ownership verification, and choose a new six-digit PIN. |
| Currently used only in the WhatsApp Business mobile app | Not through this standard flow | It requires Meta's separate Coexistence onboarding product and account eligibility. |
| Consumer WhatsApp number or legacy/on-premises deployment | Not directly | Follow Meta's applicable migration/eligibility process first. |

Meta-controlled OTP, consent, eligibility, business verification, display-name review, and payment steps cannot be automated or bypassed by this application.

## What you need

- A Meta Business Portfolio and Facebook user with administrator access to the business assets being connected.
- A Meta Developer Business app with the WhatsApp product and Facebook Login for Business.
- A new Facebook Login for Business configuration using the Embedded Signup variation and Cloud API product.
- Advanced Access to `whatsapp_business_management` and `whatsapp_business_messaging` for onboarding businesses outside the app's own test roles. Tech Provider App Review can also require `business_management`; follow the permissions shown for your Meta app and onboarding model.
- A public application domain with a valid TLS certificate. Meta will not deliver production webhooks to the local Compose HTTP address or a self-signed endpoint.
- A privacy-policy URL, data-deletion path, business verification, and App Review material when required to move the app to Live mode for external customers.
- Access to the selected phone for Meta's SMS or voice verification, plus the existing two-step PIN when reconnecting an already registered Cloud API number.
- A billing/payment configuration and approved message templates when your WhatsApp use case requires business-initiated conversations.

## Configure Meta

Meta changes dashboard labels occasionally; use its current [Embedded Signup v4 guide](https://developers.facebook.com/documentation/business-messaging/whatsapp/embedded-signup/version-4) and [implementation guide](https://developers.facebook.com/documentation/business-messaging/whatsapp/embedded-signup/implementation) as the source of truth.

1. In Meta for Developers, create or open the Business app and add **WhatsApp** plus **Facebook Login for Business**.
2. Create a Facebook Login for Business configuration with the **WhatsApp Embedded Signup** variation and **Cloud API** product. Select the WABA asset and request `whatsapp_business_management` and `whatsapp_business_messaging`. Keep the generated configuration ID.
3. Enable Client OAuth Login, Web OAuth Login, Enforce HTTPS, Embedded Browser OAuth Login, Strict Mode for redirect URIs, and Login with the JavaScript SDK. Add every deployed HTTPS host to the allowed JavaScript SDK domains and valid OAuth redirect URIs.
4. In the WhatsApp/Webhooks configuration, set the callback URL to `https://YOUR_DOMAIN/api/v1/webhooks/whatsapp`. Choose a long random verify token and put the same exact value in `META_WHATSAPP_WEBHOOK_VERIFY_TOKEN`.
5. Subscribe the app's WhatsApp webhook fields to at least `messages`. External operators should also monitor Meta's `account_update`/integration lifecycle events when onboarding customers; this release acknowledges non-message events but only creates chat work from `messages`. Northstar calls `POST /<WABA_ID>/subscribed_apps` after signup to attach the selected WABA to the app.
6. Put the app in the correct development/live mode. Development mode is limited to app-role users and test assets; onboarding unrelated customers requires the relevant business verification, App Review, and Advanced Access.

Use Meta's current v4 session payload fields so the browser receives the `WA_EMBEDDED_SIGNUP` event containing the selected WABA and phone-number IDs. Keep `featureType` absent for the standard Cloud API flow; setting it to `whatsapp_business_app_onboarding` starts the separate Coexistence flow. Do not replace `config_id` with a free-form permission scope. The frontend launches signup with:

```javascript
FB.login(callback, {
  config_id: configurationId,
  response_type: "code",
  override_default_response_type: true,
  extras: {
    sessionInfoVersion: "3",
    version: "v4",
    setup: {},
  },
});
```

## Configure Northstar

Add these values to the deployment secret store or local ignored `.env` file. None of them belongs in frontend build variables.

| Variable | Purpose |
| --- | --- |
| `META_APP_ID` | Public identifier of the Meta Business app. |
| `META_APP_SECRET` | Server-only Meta App Secret used for code exchange and webhook signatures. |
| `META_WHATSAPP_CONFIGURATION_ID` | Facebook Login for Business Embedded Signup v4 configuration ID. |
| `META_WHATSAPP_WEBHOOK_VERIFY_TOKEN` | Random shared string used only for Meta's GET callback verification. Use at least 24 characters. |
| `META_WHATSAPP_TOKEN_ENCRYPTION_KEY` | Independent random secret used to encrypt customer access tokens at rest. Use at least 32 characters. |
| `META_GRAPH_API_VERSION` | Versioned Graph API prefix; this release defaults to `v26.0`. Review it before each Meta version retirement. |
| `META_GRAPH_BASE_URL` | Must remain `https://graph.facebook.com` in production. |
| `META_WEBHOOK_MAX_BYTES` | Maximum accepted raw webhook size; default 1 MiB. |
| `META_SIGNUP_SESSION_TTL_SECONDS` | Lifetime of the signed, single-use local signup correlation token; default 600 seconds. |
| `WHATSAPP_DISPATCH_MAX_ATTEMPTS` | Maximum RabbitMQ publication attempts before a durable receipt is marked `dispatch_failed`; default 8. |

Generate the two Northstar-owned secrets independently, for example:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

After changing the environment, rebuild/restart the API, worker, and web services. Confirm that the API is ready and that the Integrations page reports WhatsApp as **Ready**. The App Secret and both Northstar-owned secrets are deliberately never returned by the bootstrap endpoint.

## Connection flow implemented here

1. An authenticated workspace owner opens **Integrations -> WhatsApp -> Connect** and chooses an active Northstar agent plus the required six-digit PIN.
2. The backend returns the public App/configuration IDs and a short-lived signed signup session bound to that user and tenant.
3. The browser preloads Meta's SDK, opens `FB.login` directly from the button click, and accepts Embedded Signup messages only from the exact Facebook origins.
4. Meta lets the owner select a business, WABA, and existing Cloud API phone number or add/verify a new number. Meta returns a one-time authorization code separately from the WABA and phone-number IDs.
5. The browser immediately sends the code, selected IDs, agent ID, PIN, and signed signup session to the authenticated completion endpoint. The code is deliberately never stored in browser storage.
6. The backend atomically consumes the signup session in Redis, exchanges the short-lived code server-to-server, validates the token's app and permissions, verifies that the selected phone belongs to the selected WABA, registers it with the PIN, and subscribes the app to the WABA.
7. Only after every Meta check succeeds does Northstar encrypt and store the customer token and mark the workspace connected. Browser-supplied WABA/phone IDs are never trusted without Graph API ownership validation.

If Redis is unavailable, production fails the single-use signup check closed. The connection must be restarted rather than weakening replay protection.

## Message flow and reliability

Inbound webhook processing is deliberately split from AI generation:

1. Meta sends a webhook to `/api/v1/webhooks/whatsapp`.
2. The API reads a bounded raw body, verifies `X-Hub-Signature-256` with HMAC-SHA256 and the App Secret using constant-time comparison, and only then parses JSON.
3. Each provider message ID is durably stored once in PostgreSQL. Duplicate Meta retries return success without creating a second business record.
4. The API publishes the receipt ID to the durable `whatsapp.inbound` RabbitMQ queue. If broker publication fails, the database recovery dispatcher republishes it later.
5. Workers serialize inbound AI work and outbound human work by connection/customer thread, reuse the open WhatsApp conversation, call the grounded NVIDIA model path, and split text at WhatsApp's 4,096-character limit.
6. Human replies created in the conversation inbox are stored first and delivered through `whatsapp.outbound`; they are not falsely marked sent before Meta accepts them.

RabbitMQ is the command queue. Kafka continues to carry committed integration/conversation events for replayable analytics and downstream consumers. Redis supplies rate limiting and single-use signup coordination; it is not used as the message system of record.

Workers and Graph API calls are at-least-once. The implementation prevents duplicate database processing with provider IDs and delivery records, but a provider timeout after Meta accepted a request can never offer mathematical exactly-once delivery. Monitor rare duplicate-delivery risk during provider/network incidents.

An accepted Graph send is recorded as `sent`. Meta delivery/read status webhook elements are safely acknowledged but are not yet projected into separate `delivered` or `read` states; add that projection if those states are required for support analytics or SLAs.

## WhatsApp policy behavior

An inbound customer message opens Meta's 24-hour customer-service window. The automated reply is produced from that inbound event. Free-form human replies are allowed only while that window remains open; outside it, use an approved WhatsApp template. Northstar rejects an out-of-window free-form human delivery instead of repeatedly sending a request Meta will refuse. Template creation/selection is not part of this release.

Meta requires Embedded Signup phone registration within its current registration window; Northstar registers immediately during completion. The PIN is sent directly to Meta over TLS and is not persisted by Northstar. See Meta's official [phone registration request](https://www.postman.com/meta/whatsapp-business-platform/request/zb2u18b/register-phone) and [WABA subscription request](https://www.postman.com/meta/whatsapp-business-platform/request/0yubu4i/subscribe-app-to-whatsapp-business-account).

## Disconnect and credential lifecycle

**Disconnect** deletes Northstar's encrypted local credential and route. It does not delete/deregister the customer's number, revoke the Facebook user's permissions, or unsubscribe the entire WABA. A WABA subscription and business token can cover other numbers or workspaces, so destructive remote cleanup from one workspace would be unsafe. Valid later events for a locally disconnected number are signature-verified, acknowledged, and ignored.

If a customer wants to remove the app from the whole business, do that in Meta Business Settings only after confirming no other number relies on it. If the customer wants to deregister or migrate a number, use Meta's dedicated process; it is intentionally not hidden behind Northstar's ordinary Disconnect button.

Token expiry is checked when displaying status and resolving webhooks. An expired authorization is shown as **Reconnect required**. Disconnect it locally and run Embedded Signup again. Changing `META_WHATSAPP_TOKEN_ENCRYPTION_KEY` makes existing ciphertext unreadable, so reconnect existing workspaces as part of a planned key rotation; never overwrite the key casually.

## Production checklist

- [ ] Meta app/configuration IDs match the same Business app whose secret is deployed.
- [ ] App is in the required mode and permissions have Advanced Access/App Review where applicable.
- [ ] Allowed SDK domains and OAuth redirect URIs exactly match the production HTTPS hosts.
- [ ] Webhook callback is the canonical HTTPS `/api/v1/webhooks/whatsapp` path and verification succeeds.
- [ ] `messages` and operational account-update fields are configured in Meta.
- [ ] Meta secrets are injected by a secret manager, excluded from image layers/logs, and rotated under a tested runbook.
- [ ] PostgreSQL migration `0002_whatsapp_integration` has completed before API/worker rollout.
- [ ] Redis persistence/no-eviction and production fail-closed mode are enabled.
- [ ] RabbitMQ queues, failed queue, recovery dispatcher, worker age, and connection `last_error` are monitored.
- [ ] API and workers have outbound TLS access to `graph.facebook.com`; only the public API webhook path needs inbound exposure.
- [ ] The Meta webhook retry/duplicate path and one real test-number conversation have been exercised before customer traffic.
- [ ] Privacy retention/deletion, transcript access, approved templates, opt-in, and escalation processes match the business's policy obligations.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| The modal says Meta setup is required | All required `META_*` values must be present together; rebuild/restart the services after adding them. |
| Facebook popup is blocked | Start it only with the button and allow popups for the application origin. Do not invoke signup automatically on page load. |
| Meta does not show the expected WABA/number | The Facebook user needs business-asset access; confirm the Embedded Signup configuration's asset/product settings and number eligibility. |
| New number cannot verify | Confirm SMS/voice access, country/use-case eligibility, and that the number is not still attached to an incompatible WhatsApp deployment. |
| Completion returns 409 | Publish the selected agent, disconnect a different existing workspace number first, or restart if the single-use signup session was already consumed. |
| Completion returns 502 | Inspect Meta dashboard status and safe server logs; repeat Embedded Signup to obtain a fresh short-lived code. |
| Webhook verification fails | Callback URL, verify token, TLS certificate, ingress route, and Meta field subscription must match exactly. |
| Messages arrive but no answer is sent | Check the durable receipt status, RabbitMQ worker/recovery dispatcher, Redis, NVIDIA availability, token expiry, and connection `last_error`. |
| Human reply is rejected | The last customer message is outside the 24-hour service window; send an approved template through a template-capable workflow. |

## Official references

- [Embedded Signup overview](https://developers.facebook.com/documentation/business-messaging/whatsapp/embedded-signup/overview)
- [Embedded Signup v4](https://developers.facebook.com/documentation/business-messaging/whatsapp/embedded-signup/version-4)
- [Onboarding customers as a Tech Provider](https://developers.facebook.com/documentation/business-messaging/whatsapp/embedded-signup/onboarding-customers-as-a-tech-provider)
- [WhatsApp webhook endpoint](https://developers.facebook.com/documentation/business-messaging/whatsapp/webhooks/create-webhook-endpoint)
- [WhatsApp access tokens](https://developers.facebook.com/documentation/business-messaging/whatsapp/access-tokens)
- [Meta's WhatsApp Cloud API collection](https://www.postman.com/meta/whatsapp-business-platform/documentation/wlk6lh4/whatsapp-cloud-api)
