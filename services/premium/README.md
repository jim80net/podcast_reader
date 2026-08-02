# Premium development service

This is the isolated online tier for accounts and entitlements. It does not
import the transcript engine and its API has no transcript, audio, source URL,
or library-content fields. The desktop application's local-only path does not
contact this service.

Slices 1 and 2 provide development accounts, hardened browser sessions, native
device authorization, rotating tokens, the frozen entitlement v1 fixtures, an
append-only entitlement ledger with a rebuildable projection, registered feature
flags, house-only ad configuration, and a server-rendered audited admin panel.
Test purchases and provider webhook processing arrive in a later independently
gated slice.

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
uv run premium-dev migrate
uv run premium-dev bootstrap-admin
uv run premium-dev serve
```

The device approval page is `/device`. Administrators sign in at `/admin/login`.
Admin mutations require the session-bound synchronizer token plus exact Origin and
Host checks. Feature keys are code-registered, premium capabilities cannot be
granted to free accounts by flag configuration, and the ad schema can represent
only plain-text house inventory with HTTPS calls to action.

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
