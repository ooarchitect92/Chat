# Meta Social Technologies MCP

Northstar includes project-level configuration for Meta's official remote Developer Tools MCP:

```text
https://mcp.facebook.com/devtools
```

Meta now calls this service the **Meta Social Technologies MCP**. It is currently beta and is intended for developers and operators managing apps on Meta for Developers. It complements Northstar's WhatsApp Embedded Signup; it does not replace customer Facebook Login, select a customer's WhatsApp number, or provide an application runtime dependency.

## Supported developer workflows

After authenticating with a Meta developer account that can access the application, an MCP-capable coding client can help with:

- Meta app health and configuration audits
- webhook configuration, testing, and delivery troubleshooting
- App Review preparation and submission history
- compliance checks and required actions
- Graph API health, call volume, rate limits, and deprecation warnings
- official Meta developer documentation search
- access-token diagnosis without placing the token in the model conversation

The Meta account still controls which apps and actions are visible. MCP does not grant additional permissions.

## Connect from Codex

The repository's `.mcp.json` declares the server for clients that support project discovery. If Codex does not discover project MCP configuration, add this to the user's Codex configuration file:

```toml
[mcp_servers.devtools]
url = "https://mcp.facebook.com/devtools"
```

Restart Codex, invoke a Meta developer workflow, and complete Meta's OAuth prompt on first use. Never paste an App Secret, system-user token, or WhatsApp access token into chat.

## Connect from Cursor

The repository includes `.cursor/mcp.json`. Open Cursor's MCP settings, enable `devtools`, and complete the OAuth flow. If the workspace is not trusted, Cursor may intentionally avoid loading project MCP configuration.

## Connect from Claude Code

Install Meta's official plugin and authenticate its bundled MCP server:

```text
claude plugin marketplace add facebook/agentic-tools
claude plugin install devtools@facebook
```

Then open `/mcp`, connect `devtools`, and complete OAuth.

## Recommended Northstar workflow

1. Use the MCP app-health check to audit the Meta Business app.
2. Use webhook setup for the public Northstar callback:

   ```text
   https://YOUR_DOMAIN/api/v1/webhooks/whatsapp
   ```

3. Confirm the WhatsApp product, Facebook Login for Business, Embedded Signup v4 configuration, permissions, and App Review status.
4. Use access-token diagnosis and webhook debugging when onboarding fails.
5. Use API-health checks during production operation.
6. Test the customer journey in Northstar: **Integrations → WhatsApp → Connect → Continue with Facebook**.

## Security and production boundaries

- OAuth is completed directly with Meta in the MCP client.
- Meta developer MCP credentials are not stored by Northstar or Docker Compose.
- Northstar server secrets remain in `.env` or the production secret manager.
- Customer WhatsApp tokens remain encrypted in the Northstar database.
- Do not expose an MCP credential through frontend JavaScript.
- A public HTTPS deployment is required for real WhatsApp webhook delivery.
- Disconnecting the MCP client does not disconnect a customer's WhatsApp number.
- Disconnecting WhatsApp in Northstar does not delete Meta business assets.

## Troubleshooting

- If no Meta apps appear, authenticate with a Meta account that has a role on the developer app.
- If OAuth does not start, restart the MCP client after adding its configuration.
- If a tool is unavailable, remember that the service is beta and tool availability depends on app permissions.
- If WhatsApp login remains disabled in Northstar, populate the five `META_*` server variables documented in `docs/whatsapp.md`; MCP authentication does not populate application runtime secrets.

## Official references

- [Meta Social Technologies MCP documentation](https://developers.facebook.com/documentation/mcp/devtools-mcp)
- [Meta Agentic Tools repository](https://github.com/facebook/agentic-tools)
- [Northstar WhatsApp setup](./whatsapp.md)
