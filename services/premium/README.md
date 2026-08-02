# Premium development service

This is the isolated online tier for accounts and entitlements. It does not
import the transcript engine and its API has no transcript, audio, source URL,
or library-content fields. The desktop application's local-only path does not
contact this service.

Slice 1 provides development accounts, hardened browser sessions, native device
authorization, rotating tokens, and the frozen entitlement v1 fixtures. Test
purchases, entitlement projection, admin pages, feature flags, and ads arrive in
later independently gated slices.

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
