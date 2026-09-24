# Gorgias Webhook Receiver

A standalone HTTP server that receives webhook events from Gorgias HTTP Integrations
and can call back to the Gorgias REST API to manage customers, tickets, and messages.

Based on the [Gorgias tutorial](https://developers.gorgias.com/docs/receive-and-respond-to-tickets-from-a-third-party-app).

## Live Endpoint

    https://srv1766050.hstgr.cloud/webhook      (HTTPS, Let's Encrypt cert)
    https://srv1766050.hstgr.cloud/health       (health check)

## Architecture

    Internet (HTTPS)
        |
        v
    Caddy (port 443) -- auto Let's Encrypt TLS, reverse proxy
        |
        v
    Python server (port 8080, localhost only) -- webhook handler + Gorgias API client
        |
        v
    Gorgias REST API (outbound HTTPS)

## Files

| File | Purpose |
|------|---------|
| `server.py` | Main HTTP server + Gorgias API client (stdlib only) |
| `config.json` | Configuration (API key stored encrypted) |
| `crypto_util.py` | API key encryption/decryption utility |
| `test_server.py` | Test suite (7 tests) |
| `gorgias-webhook.service` | systemd service for the webhook server |

## API Key Encryption

The Gorgias API key in `config.json` is stored encrypted (Fernet symmetric encryption).
The encryption key is in `/etc/gorgias-wh-key` (root-only, mode 600).

Manage encryption:
```bash
# Check status
python3 /root/gorgias-webhook/crypto_util.py status

# Encrypt a new key
python3 /root/gorgias-webhook/crypto_util.py encrypt 'your-api-key'
# -> paste output into config.json as: "gorgias_api_key": "enc:<encrypted>"

# Decrypt (for verification)
python3 /root/gorgias-webhook/crypto_util.py decrypt '<encrypted-token>'
```

## HTTPS

Caddy acts as a TLS-terminating reverse proxy. It automatically:
- Obtains a Let's Encrypt certificate for srv1766050.hstgr.cloud
- Renews the certificate before expiry
- Redirects HTTP to HTTPS
- Proxies requests to the Python server on localhost:8080

Caddy config: `/etc/caddy/Caddyfile`
Caddy logs: `/var/log/caddy/gorgias-webhook.log`
Cert location: `/var/lib/caddy/.local/share/caddy/certificates/`

## What It Does

The server listens on port 8080 and exposes three routes:

- `GET /health` — health check (returns JSON status)
- `GET /test` — human-readable test page
- `POST /webhook` — receives Gorgias webhook events

When Gorgias sends an HTTP integration webhook (ticket created, ticket updated,
message created), the server:

1. Validates the `X-Webhook-Secret` header against your configured secret
2. Parses the JSON payload and identifies the event type
3. Routes to the appropriate handler (`handle_ticket_created`, `handle_ticket_updated`, `handle_message_created`)
4. Logs the event to both `webhooks.log` and `webhook_events.jsonl`
5. Returns 200 OK so Gorgias doesn't retry

The `GorgiasClient` class in `server.py` also provides methods for calling the
Gorgias API (steps 3-6 of the tutorial):

- `list_customers()` — retrieve paginated customers
- `create_customer(name, phone, email)` — create a customer with channel info
- `update_customer(customer_id, channels)` — update customer channels
- `create_ticket(channel, sender_email, receiver_email, subject, body_text)` — create a ticket
- `get_ticket(ticket_id)` — retrieve a ticket
- `add_message_to_ticket(ticket_id, body_text)` — add a message/reply

## Configuration

Edit `config.json`:

```json
{
    "host": "0.0.0.0",
    "port": 8080,
    "gorgias_base_url": "https://YOUR-STORE.gorgias.com",
    "gorgias_username": "your-gorgias-username",
    "gorgias_api_key": "your-gorgias-api-key",
    "log_file": "/root/gorgias-webhook/webhooks.log",
    "secret_token": "change-this-to-a-random-secret"
}
```

Replace:
- `gorgias_base_url` — your Gorgias domain (e.g. `https://acme.gorgias.com`)
- `gorgias_username` — your Gorgias API username (usually your email)
- `gorgias_api_key` — your Gorgias API key (find it in Settings -> Rest API -> API Key)
- `secret_token` — a random string; Gorgias will send this in the `X-Webhook-Secret` header

## Service Management

```bash
# Start / stop / restart
systemctl start gorgias-webhook
systemctl stop gorgias-webhook
systemctl restart gorgias-webhook

# Check status
systemctl status gorgias-webhook

# View logs
journalctl -u gorgias-webhook -f

# View webhook event logs
tail -f /root/gorgias-webhook/webhooks.log
tail -f /root/gorgias-webhook/webhook_events.jsonl
```

## Gorgias HTTP Integration Setup

Once the server is running, configure Gorgias to send webhooks:

1. In Gorgias: **Settings** → **Integrations** → **HTTP Integrations** → **Create integration**
2. Set the URL to: `http://YOUR-VPS-IP:8080/webhook`
3. Add a custom header: `X-Webhook-Secret: <your secret_token from config.json>`
4. Select triggers:
   - **Ticket created** — fires when a new ticket is created
   - **Ticket message created** — fires when a new message is added (agent or customer)
5. Save

## Testing

```bash
# Run the test suite
python3 /root/gorgias-webhook/test_server.py

# Manual health check
curl http://localhost:8080/health

# Send a test webhook
curl -X POST http://localhost:8080/webhook \
  -H "Content-Type: application/json" \
  -H "X-Webhook-Secret: change-this-to-a-random-secret" \
  -d '{"event":"ticket.created","data":{"id":123,"subject":"Test ticket"}}'
```

## Security Notes

- The server uses HTTPS when calling the Gorgias API (outbound)
- The webhook endpoint itself uses HTTP (port 8080). For production, consider:
  - Putting it behind a reverse proxy (nginx/caddy) with TLS
  - Or using a Let's Encrypt certificate directly
- The `secret_token` prevents unauthorized webhook submissions
- Gorgias API credentials use Basic Auth (base64 encoded username:api_key)