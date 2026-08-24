# Caddy production layout

`Caddyfile.redacted` is the stable, import-only entrypoint. Public hostnames
live in `sites/`, one owned fragment per service boundary:

| Fragment | Hostnames owned | Upstream |
| --- | --- | --- |
| `sites/support.caddy` | `hermes.buttonsbebe.com`, `srv1766050.hstgr.cloud`, `support.buttonsbebe.com` | `127.0.0.1:8000`, `:8085`, `:8087`, `:9119` |
| `sites/exchange.caddy` | `exchange.buttonsbebe.com` | `127.0.0.1:4100` |
| `sites/warehouse.caddy` | `wh.buttonsbebe.com` | `127.0.0.1:4000` |

The tracked files are redacted templates. `<WA_TOKEN>` and
`<WAREHOUSE_PASSWORD_HASH>` are placeholders, not credentials. Production
fragments live under the root-owned Caddy configuration directory and must be
materialized from the approved secret/configuration process. Never commit a
real token, password hash, or runtime-only path.

## Ownership rule

Change a service only in its fragment. A support-console or Hermes-dashboard
change must modify `sites/support.caddy`; it must not rewrite the root
entrypoint or copy a support-only file over the complete production
configuration. Adding a new service boundary requires a new reviewed fragment,
an explicit import, and a hostname inventory review.

The CD receiver fingerprints `deploy/caddy` and stops for manual approval when
this configuration changes. That approval is the point at which the matching
production fragment set is reviewed and installed; application CD must not
silently replace Caddy configuration.

## Safe atomic apply and rollback

Use this procedure on the VPS as root after reviewing the hostname inventory.
It is intentionally fragment-based and keeps the import entrypoint stable.

1. Record the active state and make a dated rollback copy of the current
   fragment directory and import entrypoint:

   ```sh
   stamp="$(date -u +%Y%m%dT%H%M%SZ)"
   backup="/etc/caddy/rollback-$stamp"
   install -d -m 0700 "$backup"
   if [ -e /etc/caddy/sites ] || [ -L /etc/caddy/sites ]; then
     cp -a /etc/caddy/sites "$backup/sites-link"
   fi
   cp -p /etc/caddy/Caddyfile "$backup/Caddyfile"
   ```

2. Stage the complete reviewed fragment set in a new sibling directory. Copy
   unchanged fragments as well as the one being changed so the candidate is a
   complete configuration. Keep the live `sites` directory untouched while
   preparing the candidate:

   ```sh
   next="/etc/caddy/sites-$stamp"
   install -d -m 0750 "$next"
   install -o root -g caddy -m 0640 reviewed/support.caddy "$next/support.caddy"
   install -o root -g caddy -m 0640 reviewed/exchange.caddy "$next/exchange.caddy"
   install -o root -g caddy -m 0640 reviewed/warehouse.caddy "$next/warehouse.caddy"
   ```

3. Validate a temporary candidate that imports the staged directory. Validate
   before changing the live directory, and inspect the rendered hostname list
   for accidental removals:

   ```sh
   candidate="/etc/caddy/.Caddyfile-$stamp"
   printf '%s\n' \
     "import $next/support.caddy" \
     "import $next/exchange.caddy" \
     "import $next/warehouse.caddy" > "$candidate"
   caddy validate --config "$candidate" --adapter caddyfile
   caddy adapt --config "$candidate" --adapter caddyfile --pretty \
     | jq -r '.. | objects | .match?.host? // empty | .[]?' \
     | sort -u
   ```

   The host list must contain every approved hostname, including the support,
   exchange, and warehouse names. Stop if any existing hostname disappears or
   a new one appears without approval.

4. Point the stable `sites` path at the new versioned directory with one
   atomic symlink rename, install the import-only entrypoint, and reload Caddy.
   The running Caddy process keeps its last valid configuration throughout:

   ```sh
   old_sites="$(readlink /etc/caddy/sites 2>/dev/null || true)"
   next_link="/etc/caddy/.sites-link-$stamp"
   ln -s "$(basename "$next")" "$next_link"
   mv -Tf "$next_link" /etc/caddy/sites
   candidate_root="/etc/caddy/.Caddyfile-root-$stamp"
   printf '%s\n' \
     'import sites/support.caddy' \
     'import sites/exchange.caddy' \
     'import sites/warehouse.caddy' > "$candidate_root"
   install -o root -g caddy -m 0640 "$candidate_root" /etc/caddy/Caddyfile
   if ! caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile; then
       if [ -n "$old_sites" ]; then
         rollback_link="/etc/caddy/.sites-rollback-$stamp"
         ln -s "$old_sites" "$rollback_link"
         mv -Tf "$rollback_link" /etc/caddy/sites
       else
         rm -f /etc/caddy/sites
       fi
       cp -p "$backup/Caddyfile" /etc/caddy/Caddyfile
       caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile
       exit 1
   fi
   ```

5. Create a root-owned integrity manifest for the active entrypoint and exact
   fragment set. The safety monitor verifies this on every run:

   ```sh
   cd /etc/caddy
   sha256sum Caddyfile sites/support.caddy sites/exchange.caddy \
     sites/warehouse.caddy > buttonsbebe-caddy.sha256
   chown root:root buttonsbebe-caddy.sha256
   chmod 0600 buttonsbebe-caddy.sha256
   ```

6. Verify the protected console, exchange route, warehouse HTTPS boundary,
   and the webhook route. The warehouse webhook check must use a signed,
   isolated request if delivery behavior is tested; do not generate a live
   Shopify event. Keep the dated rollback directory until the checks and a
   later observation window are complete.

7. To roll back after a successful reload, atomically repoint `/etc/caddy/sites`
   to the previously reviewed versioned directory, restore the matching saved
   entrypoint if needed, validate, reload, and re-run the route checks. During
   the initial migration from a monolithic file, the dated saved Caddyfile is
   the rollback source. Never bypass validation.

The commands above do not create, update, or delete Shopify products,
variants, inventory, orders, customers, themes, or storefront content. Caddy
only controls routing and authentication; the warehouse application still
verifies Shopify webhook HMAC signatures.
