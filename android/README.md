# Podcast Reader for Android

This package is the native thin-client scaffold approved in issue #123. The
home Podcast Reader engine remains the worker; Android connects only to an
explicit private Tailscale Serve HTTPS origin.

The local reader remains independent of an online account. The premium boundary
uses a separate HTTPS origin, transport, authorizer, and Keystore-backed record;
local mode does not construct that runtime. Entitlement parsing consumes the
backend-owned `services/premium/contracts/entitlements-v1-{free,premium}.json`
fixtures directly and fails closed to online-unavailable without affecting local
reading.

Premium features consume only their backend-owned consumer fixtures. House
inventory is memory-only, eligible only
under fresh online-free truth, and rendered as inert native text in designated
Android chrome. There are no analytics, telemetry, trackers, ad SDKs, advertising
IDs, remote images, HTML, scripts, or impression/click reporting surfaces.

## Requirements

- JDK 17
- Android SDK Platform 36 and Build Tools 36.0.0

## Quality gates

~~~bash
./gradlew --no-daemon testDebugUnitTest lintDebug assembleRelease
uv run python scripts/check_engine_contract_fixtures.py  # from android/
python3 scripts/check_ad_containment.py                  # from android/
python3 scripts/check_ad_contract_handoff.py             # from android/
~~~

The first command compiles the API-28+ Compose shell, runs boundary tests,
performs Android lint, and builds the unsigned release APK. The Python parity
check proves that the Kotlin fixtures retain the exact key sets owned by the
real engine models. JVM tests also decode the premium service's committed
entitlement fixtures in place rather than maintaining Android-owned copies.
