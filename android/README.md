# Podcast Reader for Android

This package is the native thin-client scaffold approved in issue #123. The
home Podcast Reader engine remains the worker; Android connects only to an
explicit private Tailscale Serve HTTPS origin.

The current increment is deliberately local-only. It contains no premium
account, entitlement, advertisement, analytics, telemetry, or tracker code.

## Requirements

- JDK 17
- Android SDK Platform 36 and Build Tools 36.0.0

## Quality gates

~~~bash
./gradlew --no-daemon testDebugUnitTest lintDebug assembleRelease
uv run python scripts/check_engine_contract_fixtures.py  # from android/
~~~

The first command compiles the API-28+ Compose shell, runs boundary tests,
performs Android lint, and builds the unsigned release APK. The Python parity
check proves that the Kotlin fixtures retain the exact key sets owned by the
real engine models.
