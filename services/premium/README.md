# Premium development service

This is the isolated online tier for accounts and entitlements. It does not
import the transcript engine and its API has no transcript, audio, source URL,
or library-content fields. The desktop application's local-only path does not
contact this service.

Slices 1–3 provide development accounts, hardened browser sessions, native
device authorization, rotating tokens, the frozen entitlement v1 fixtures, an
append-only entitlement ledger with a rebuildable projection, registered feature
flags, house-only ad configuration, a server-rendered audited admin panel, and
Stripe-hosted test purchases with webhook-authoritative entitlement grants.

The service is deliberately development-only, listens on loopback, and supports
one worker because login limiting is process-local. It does not configure or
mutate Tailscale Serve. The existing desktop private-web Serve ownership remains
an independent surface and is protected by the repository boundary test.

In P1, `premium-dev migrate` is intentionally supported only from a
`services/premium` source checkout: the existing-infrastructure dev deployment
runs from this repository, and a packaged migration-resource contract is deferred
until the deployment slice defines the service artifact.

```bash
cd services/premium
uv sync --extra dev
export PREMIUM_USER_CODE_PEPPER='replace-with-at-least-32-random-characters'
export STRIPE_SECRET_KEY='sk_test_...'
export STRIPE_PRICE_ID='price_...'
export STRIPE_WEBHOOK_SECRET='whsec_...'
export PREMIUM_PRICE_CURRENCY='usd'
export PREMIUM_PRICE_UNIT_AMOUNT='999'
uv run premium-dev migrate
uv run premium-dev bootstrap-admin
uv run premium-dev serve
```

The device approval page is `/device`. Administrators sign in at `/admin/login`.
Admin mutations require the session-bound synchronizer token plus exact Origin and
Host checks. Feature keys are code-registered, premium capabilities cannot be
granted to free accounts by flag configuration, and the ad schema can represent
only plain-text house inventory with HTTPS calls to action.

The service rejects non-test Stripe keys at construction and rejects a live,
inactive, recurring, wrong-currency, or wrong-amount Price during startup. Stripe
webhooks are verified over their untouched raw bytes before a minimal event record
enters the durable inbox. The request never grants an entitlement; the single
restart-safe worker retrieves the authoritative Checkout Session and validates its
mode, payment, Price, quantity, Customer, amount, currency, and internal metadata.
Duplicate events and stale claims are idempotent. Full Stripe payloads and payment
details are never stored.

CI exercises the same flow through a deterministic signed fake adapter. The real
sandbox acceptance requires the running dev service plus `stripe listen` forwarding
only `checkout.session.completed,checkout.session.expired` to the loopback webhook:

```bash
stripe listen \
  --events checkout.session.completed,checkout.session.expired \
  --forward-to http://127.0.0.1:8787/v1/webhooks/stripe
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
revision. Dev-instance activation and any reverse-proxy wiring are separate XO
operations after merge; this slice does not deploy.

For that later activation, capture the existing private-web Serve status before
deployment and assert the same canonical status afterward. The acceptance helper
only runs `tailscale serve status --json`; it never invokes a mutating command:

```bash
uv run python scripts/assert_private_web_serve.py capture --baseline /tmp/serve-before.json
# XO-owned dev deployment happens here.
uv run python scripts/assert_private_web_serve.py assert --baseline /tmp/serve-before.json
```

Any mismatch fails the acceptance gate and leaves remediation to the existing
private-web owner instead of attempting a repair from this service.
