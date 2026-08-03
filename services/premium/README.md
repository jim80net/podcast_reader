# Premium development service

This is the isolated online tier for accounts and entitlements. The desktop
application's local-only path does not contact this service. When a person
explicitly requests transcript email—or enables email on subscription
completion—the client sends one bounded plain-text transcript and title to this
service for immediate delivery. The relay keeps no transcript copy: request
content lives only in application memory while it is written to the configured
DEV Maildir destination, which retains the resulting email. Audio, feed URLs,
source URLs, local paths, recipient addresses, attachments, and HTML never enter
the relay API.

Slices 1–3 provide development accounts, hardened browser sessions, native
device authorization, rotating tokens, the frozen entitlement v1 fixtures, an
append-only entitlement ledger with a rebuildable projection, registered feature
flags, house-only ad configuration, a server-rendered audited admin panel, and
Stripe-hosted test purchases with webhook-authoritative entitlement grants.

The service is deliberately development-only, listens on loopback, and supports
one worker because login limiting is process-local. It does not configure or
mutate Tailscale Serve. The existing desktop private-web Serve ownership remains
an independent surface and is protected by the repository boundary test.

`premium-dev migrate` is intentionally supported from a `services/premium` source
tree. The deployment helper installs an immutable `git archive` of the gated commit
and builds that tree from `uv.lock`; the supervised service never follows a moving
checkout.

```bash
cd services/premium
uv sync --extra dev
export PREMIUM_USER_CODE_PEPPER='replace-with-at-least-32-random-characters'
export STRIPE_SECRET_KEY='sk_test_...'
export STRIPE_PRICE_ID='price_...'
export STRIPE_WEBHOOK_SECRET='whsec_...'
export PREMIUM_PRICE_CURRENCY='usd'
export PREMIUM_PRICE_UNIT_AMOUNT='999'
export PREMIUM_EMAIL_MAILDIR='/absolute/private/path/to/maildir'
export PREMIUM_EMAIL_DELIVERY_HMAC_KEY='replace-with-at-least-32-random-characters'
uv run premium-dev migrate
uv run premium-dev bootstrap-admin
uv run premium-dev serve # 127.0.0.1:8090, one worker
```

The device approval page is `/device`. Administrators sign in at `/admin/login`.
Admin mutations require the session-bound synchronizer token plus exact Origin and
Host checks. Feature keys are code-registered, premium capabilities cannot be
granted to free accounts by flag configuration, and the ad schema can represent
only plain-text house inventory with HTTPS calls to action.

`POST /v1/email-deliveries` is bearer-authenticated and re-evaluates the frozen
`transcript_email` capability for every request. It accepts no recipient: the DEV
sink always addresses `dev-mailbox@podcast-reader.invalid`, creates an owner-only
Maildir outside the checkout, and has no SMTP or provider configuration. Durable
receipts contain identifiers, consent kind, byte count, an HMAC of the canonical
payload, fixed sink/state/error values, attempts, and timestamps—never title,
transcript, account address, raw content digest, feed/source data, or message body.

The service rejects non-test Stripe keys at construction and rejects a live,
inactive, recurring, wrong-currency, or wrong-amount Price during startup. Stripe
webhooks are verified over their untouched raw bytes before a minimal event record
enters the durable inbox. The request never grants an entitlement; the single
restart-safe worker retrieves the authoritative Checkout Session and validates its
mode, payment, Price, quantity, Customer, amount, currency, and internal metadata.
Duplicate events and stale claims are idempotent. Provider failures use deferred
retries so newer events can proceed; an exhausted event is parked with its bounded
attempt count and result code visible to administrators. Full Stripe payloads and
payment details are never stored.

CI exercises the same flow through a deterministic signed fake adapter. The real
sandbox acceptance requires the running dev service plus `stripe listen` forwarding
only `checkout.session.completed,checkout.session.expired` to the loopback webhook:

```bash
stripe listen \
  --events checkout.session.completed,checkout.session.expired \
  --forward-to http://127.0.0.1:8090/v1/webhooks/stripe
uv run python scripts/stripe_sandbox_smoke.py \
  --base-url https://premium-tailnet-host.example.ts.net:8443 \
  --email reader@example.com
```

The smoke prompts for the account password without placing it on the command line,
opens no public ingress, prints the Stripe-hosted test URL, and passes only after the
forwarded signed event changes the server-rendered account tier to premium.

`GET /v1/me/entitlements` is the canonical bearer-authenticated client projection.
Its committed schema-version-1 fixtures under `contracts/` are frozen; any contract
change requires a schema-version bump and an independent design gate.

The service refuses to start before Alembic is at exactly the expected schema
revision.

## Existing-host deployment runbook

This runbook is for the zero-spend private dev instance only. It never invokes
Tailscale Funnel. Preparation is intentionally separate from activation: the desk
may install and prove the loopback service, but only the XO adds the Serve listener
and runs the private-URL acceptance smoke.

The committed deployment pins Stripe CLI `v1.45.0` for Linux arm64 to the upstream
archive SHA-256
`1be10d41ac0712978e1abfe1bae1223af88bebc3929a088ee8fc9d099b570e5d`.
The helper downloads that immutable GitHub release, checks the digest and single
archive member, installs it under the private service data directory, and verifies
the reported version. It also snapshots the exact git commit, builds with
`uv sync --frozen --no-dev`, generates the persistent user-code pepper locally,
captures the pre-existing Serve configuration, stops on an occupied HTTPS listener,
and installs two hardened user-systemd units. It does not create a listener or
enable either unit.

From a clean checkout at the gated commit on the target host:

```bash
cd services/premium
BUILD_SHA=$(git rev-parse HEAD)
PUBLIC_ORIGIN=https://rt-dgx-sp001.taild1140e.ts.net:8443
uv run python scripts/dev_host.py prepare \
  --home /home/jim \
  --checkout-root ../.. \
  --public-origin "$PUBLIC_ORIGIN" \
  --https-port 8443 \
  --loopback-port 8090 \
  --build-sha "$BUILD_SHA" \
  --uv /home/jim/.local/bin/uv
```

The preparation evidence is owner-only at
`~/.local/state/podcast-reader-premium/prepare-evidence.json`; it contains hashes,
paths, versions, and booleans but no secret values. `service.env` is also owner-only.
No static browser-session or CSRF secret exists: those tokens are generated per
record. The persistent user-code pepper and content-free delivery-receipt HMAC key
are the only non-Stripe secrets generated by this step. Preparation also fixes the
DEV Maildir under the private service data directory; no external email is possible.

Before each migration, take and restore-prove an online SQLite backup if the database
already exists. On a first install, record that no pre-migration database exists,
run the migrations, then create the first proof backup:

```bash
DATA=/home/jim/.local/share/podcast-reader-premium
STATE=/home/jim/.local/state/podcast-reader-premium
RELEASE="$DATA/releases/$BUILD_SHA/services/premium"
ENV=/home/jim/.config/podcast-reader-premium/service.env
DATABASE="$DATA/premium.sqlite3"
if test -f "$DATABASE"; then
  set -a; . "$ENV"; set +a
  "$RELEASE/.venv/bin/python" "$RELEASE/scripts/dev_host.py" backup \
    --database "$DATABASE" --output "$STATE/pre-migration-$BUILD_SHA.sqlite3"
fi
set -a; . "$ENV"; set +a
"$RELEASE/.venv/bin/premium-dev" --database "$DATABASE" \
  --public-origin "$PUBLIC_ORIGIN" migrate
"$RELEASE/.venv/bin/python" "$RELEASE/scripts/dev_host.py" backup \
  --database "$DATABASE" --output "$STATE/post-migration-$BUILD_SHA.sqlite3"
```

The backup command uses SQLite's online-backup API, opens the result read-only,
requires `PRAGMA integrity_check = ok`, and compares every application-table row
count. Both backup and JSON manifest are mode `0600`.

Create the initial administrator from a real terminal on the host. The CLI prompts
for the email and masks both password entries; the password must not be placed in an
environment variable, command argument, shell history, or deployment log:

```bash
"$RELEASE/.venv/bin/premium-dev" --database "$DATABASE" \
  --public-origin "$PUBLIC_ORIGIN" bootstrap-admin
```

Stripe credentials are the activation boundary. After the XO resolves the sandbox
key and Price and obtains the CLI `whsec_…`, install them through masked prompts;
the command rejects live keys and unsafe/malformed values:

```bash
"$RELEASE/.venv/bin/python" "$RELEASE/scripts/dev_host.py" \
  install-stripe-credentials --home /home/jim
systemctl --user enable --now premium-dev.service premium-stripe-forwarder.service
curl --fail --silent http://127.0.0.1:8090/healthz
```

The premium unit is conditioned on that credential file, starts one Uvicorn worker
on loopback, and fails closed during the real Stripe Price preflight. The forwarder
uses only `checkout.session.completed` and `checkout.session.expired`, targets only
the loopback webhook, and receives its test API key through the owner-only
environment file rather than argv. Until credentials exist, both units remain
loaded but stopped; a synthetic billing mode is deliberately not available in dev.

XO activation first runs the non-mutating conflict check, then creates exactly one
private Serve mapping:

```bash
"$RELEASE/.venv/bin/python" "$RELEASE/scripts/assert_private_web_serve.py" \
  check-conflict --baseline "$STATE/serve-before.json" --new-https-port 8443
tailscale serve --bg --https=8443 http://127.0.0.1:8090
"$RELEASE/.venv/bin/python" "$RELEASE/scripts/assert_private_web_serve.py" \
  assert --baseline "$STATE/serve-before.json" --new-https-port 8443 \
  --new-target http://127.0.0.1:8090
```

The final assertion removes only that exact new listener before comparing the
canonical JSON baseline. A changed existing private-web mapping, wrong target,
non-HTTPS listener, duplicate mapping, or conflict fails closed; the helper never
attempts repair. The XO then proves no Funnel, confirms port 8090 is loopback-only,
runs `stripe_sandbox_smoke.py` through the private URL, and replays the full
free→purchase→premium→override→disable acceptance sequence from the approved design.
