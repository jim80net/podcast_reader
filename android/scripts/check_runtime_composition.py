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
        "PremiumNativeAuthTransport(requests, premiumClient)",
        "PremiumCurrentUserTransport(requests, premiumClient)",
        "PremiumEntitlementTransport(requests, premiumClient)",
    ):
        require(required in composition, f"production runtime dependency missing: {required}")

    allowed = COMPOSITION.resolve()
    definitions = {
        "PremiumAccountAuthorizer": "PremiumAccountAuthorizer.kt",
        "PremiumNativeAuthTransport": "PremiumNativeAuthTransport.kt",
        "PremiumCurrentUserTransport": "PremiumTransport.kt",
        "PremiumEntitlementTransport": "PremiumTransport.kt",
    }
    kotlin_files = list(MAIN.rglob("*.kt"))
    for class_name, definition_name in definitions.items():
        marker = f"{class_name}("
        declaration = f"class {marker}"
        occurrences = []
        for path in kotlin_files:
            source = path.read_text()
            if path.name == definition_name:
                require(
                    source.count(declaration) == 1,
                    f"expected one declaration for {class_name} in {definition_name}",
                )
                source = source.replace(declaration, "", 1)
            occurrences.extend([path.resolve()] * source.count(marker))
        require(
            occurrences == [allowed],
            f"alternate production constructor for {marker}: {list(map(str, occurrences))}",
        )


if __name__ == "__main__":
    main()
