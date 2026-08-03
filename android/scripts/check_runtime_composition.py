"""Fail if Android regains an alternate production premium composition path."""

from __future__ import annotations

from pathlib import Path


ANDROID = Path(__file__).resolve().parents[1]
MAIN = ANDROID / "app" / "src" / "main" / "java"
ACTIVITY = MAIN / "net" / "jim80" / "podcastreader" / "MainActivity.kt"
COMPOSITION = MAIN / "net" / "jim80" / "podcastreader" / "runtime" / "PodcastReaderViewModel.kt"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(message)


def main() -> None:
    activity = ACTIVITY.read_text()
    require("PodcastReaderProductionComposition.viewModelFactory" in activity, "MainActivity must use the runtime owner")
    for forbidden in (
        "ProductStateReducer",
        "PodcastReaderUiState.project",
        "PodcastReaderActions(",
        "onConnectAccount = {}",
        "onRetryAccount = {}",
        "onSignOut = {}",
    ):
        require(forbidden not in activity, f"alternate MainActivity truth/action construction: {forbidden}")

    composition = COMPOSITION.read_text()
    for required in (
        "EngineCredentialStore.create(applicationContext)",
        "PremiumCredentialStore.create(applicationContext)",
        "PremiumNativeAuthTransport(requests, client)",
        "PremiumCurrentUserTransport(requests, client)",
        "PremiumEntitlementTransport(requests, client)",
    ):
        require(required in composition, f"production runtime dependency missing: {required}")

    allowed = COMPOSITION.resolve()
    definition_files = {
        "PremiumAccountAuthorizer(": "PremiumAccountAuthorizer.kt",
        "PremiumNativeAuthTransport(": "PremiumNativeAuthTransport.kt",
        "PremiumCurrentUserTransport(": "PremiumTransport.kt",
        "PremiumEntitlementTransport(": "PremiumTransport.kt",
    }
    kotlin_files = list(MAIN.rglob("*.kt"))
    for marker, definition_name in definition_files.items():
        callers = {
            path.resolve()
            for path in kotlin_files
            if path.name != definition_name and marker in path.read_text()
        }
        require(callers == {allowed}, f"alternate production constructor for {marker}: {sorted(map(str, callers))}")


if __name__ == "__main__":
    main()
