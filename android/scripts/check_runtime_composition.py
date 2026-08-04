"""Fail if Android regains an alternate production premium composition path."""

from __future__ import annotations

import re
from pathlib import Path

ANDROID = Path(__file__).resolve().parents[1]
MAIN = ANDROID / "app" / "src" / "main" / "java"
ACTIVITY = MAIN / "net" / "jim80" / "podcastreader" / "MainActivity.kt"
COMPOSITION = MAIN / "net" / "jim80" / "podcastreader" / "runtime" / "PodcastReaderViewModel.kt"
RUNTIME = MAIN / "net" / "jim80" / "podcastreader" / "runtime" / "PodcastReaderRuntime.kt"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(message)


def repository_constructed_inside_runtime_gate(source: str) -> bool:
    gate_marker = "HouseAdRuntimeGate.create(productState, now())"
    if source.count(gate_marker) != 1:
        return False
    gate_start = source.index(gate_marker)
    block_start = source.find("{", gate_start + len(gate_marker))
    if block_start == -1:
        return False
    depth = 0
    block_end = -1
    for index in range(block_start, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                block_end = index
                break
    constructors = [match.start() for match in re.finditer(r"\bHouseAdRepository\s*\(", source)]
    return len(constructors) == 1 and block_start < constructors[0] < block_end


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
        "AndroidExternalBrowserLauncher(applicationContext)",
        "DeviceAuthorizationFlow(origin, nativeAuth, browser)",
        "HouseAdTransport(HouseAdRequestFactory(origin), access, premiumClient)",
        "AndroidHouseAdCtaLauncher(applicationContext)",
        "accountConnectionFactory = PremiumAccountConnectionFactory",
    ):
        require(required in composition, f"production runtime dependency missing: {required}")
    require('"https://' not in composition, "production composition must not embed a premium origin")
    runtime = RUNTIME.read_text()
    require(
        repository_constructed_inside_runtime_gate(runtime),
        "house-ad repository construction must stay reducer-gated",
    )
    constructor = "HouseAdRepository(eligibility, api, now)"
    relocated_body = runtime.replace(constructor, "api", 1)
    require(
        relocated_body != runtime,
        "runtime gate negative control did not relocate the constructor",
    )
    relocated = relocated_body + f"\n{constructor}\n"
    require(
        not repository_constructed_inside_runtime_gate(relocated),
        "runtime gate negative control failed",
    )

    allowed = COMPOSITION.resolve()
    definitions = {
        "PremiumAccountAuthorizer": "PremiumAccountAuthorizer.kt",
        "PremiumNativeAuthTransport": "PremiumNativeAuthTransport.kt",
        "PremiumCurrentUserTransport": "PremiumTransport.kt",
        "PremiumEntitlementTransport": "PremiumTransport.kt",
        "AndroidExternalBrowserLauncher": "DeviceAuthorizationFlow.kt",
        "DeviceAuthorizationFlow": "DeviceAuthorizationFlow.kt",
        "HouseAdTransport": "HouseAdTransport.kt",
        "AndroidHouseAdCtaLauncher": "HouseAdCard.kt",
    }
    kotlin_files = list(MAIN.rglob("*.kt"))
    for class_name, definition_name in definitions.items():
        marker = f"{class_name}("
        constructor_pattern = re.compile(rf"\b{re.escape(class_name)}\s*\(")
        declaration_pattern = re.compile(rf"\bclass\s+{re.escape(class_name)}\s*\(")
        occurrences = []
        for path in kotlin_files:
            source = path.read_text()
            if path.name == definition_name:
                require(
                    len(declaration_pattern.findall(source)) == 1,
                    f"expected one declaration for {class_name} in {definition_name}",
                )
                source = declaration_pattern.sub("", source, count=1)
            occurrences.extend([path.resolve()] * len(constructor_pattern.findall(source)))
        require(
            occurrences == [allowed],
            f"alternate production constructor for {marker}: {list(map(str, occurrences))}",
        )

    account_factory_pattern = re.compile(r"\bPremiumAccountConnectionFactory\s*\{")
    account_factory_declaration = re.compile(r"\bfun\s+interface\s+PremiumAccountConnectionFactory\s*\{")
    account_factory_occurrences = []
    for path in kotlin_files:
        source = path.read_text()
        if path.name == "PodcastReaderRuntime.kt":
            require(
                len(account_factory_declaration.findall(source)) == 1,
                "expected one PremiumAccountConnectionFactory declaration",
            )
            source = account_factory_declaration.sub("", source, count=1)
        account_factory_occurrences.extend(
            [path.resolve()] * len(account_factory_pattern.findall(source)),
        )
    require(
        account_factory_occurrences == [allowed],
        "alternate production PremiumAccountConnectionFactory construction: "
        f"{list(map(str, account_factory_occurrences))}",
    )

    repository_pattern = re.compile(r"\bHouseAdRepository\s*\(")
    repository_declaration = re.compile(r"\bclass\s+HouseAdRepository\s*\(")
    repository_occurrences = []
    for path in kotlin_files:
        source = path.read_text()
        if path.name == "HouseAdRepository.kt":
            require(
                len(repository_declaration.findall(source)) == 1,
                "expected one HouseAdRepository declaration",
            )
            source = repository_declaration.sub("", source, count=1)
        repository_occurrences.extend(
            [path.resolve()] * len(repository_pattern.findall(source)),
        )
    require(
        repository_occurrences == [RUNTIME.resolve()],
        "house-ad repository must be constructed only by the runtime gate: "
        f"{list(map(str, repository_occurrences))}",
    )


if __name__ == "__main__":
    main()
