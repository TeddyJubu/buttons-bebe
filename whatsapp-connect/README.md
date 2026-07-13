# WhatsApp Connect

The service binds to `127.0.0.1:8085` and is exposed through Caddy. It requires
three different secrets from `/root/Buttonsbebe Agent/whatsapp-connect/.env` and refuses to start
when any is missing or still uses a placeholder:

```dotenv
WA_TOKEN=<random 32+ character path token>
WA_PASSWORD=<random 16+ character pairing-page password>
WA_SEND_SECRET=<random 32+ character escalation sender secret>
```

`POST /connect-whatsapp/<WA_TOKEN>/send` requires `WA_SEND_SECRET` in either:

- `Authorization: Bearer <WA_SEND_SECRET>` (preferred), or
- the password portion of HTTP Basic authentication, for compatibility with a
  `WHATSAPP_SEND_URL` that embeds credentials.

The processor must be configured with one of those authentication methods before
the service is restarted. The path token alone never authorizes a send.

Keep this dedicated file readable only by the service account (`chmod 600`). Do
not point the service at the shared application `.env`; the WhatsApp/Hermes process
does not need Gorgias, Shopify, or Redo credentials.
