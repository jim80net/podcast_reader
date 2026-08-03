"""Fail the Android build if an ad/tracker or executable creative surface appears."""

from __future__ import annotations

from pathlib import Path
from xml.etree import ElementTree


ANDROID = Path(__file__).resolve().parents[1]
MAIN = ANDROID / "app" / "src" / "main"
ANDROID_NAME = "{http://schemas.android.com/apk/res/android}name"

FORBIDDEN_DEPENDENCIES = (
    "play-services-ads",
    "play-services-analytics",
    "com.android.installreferrer",
    "facebook-android-sdk",
    "com.adjust.sdk",
    "appsflyer",
    "firebase-analytics",
    "firebase-crashlytics",
    "io.sentry",
    "com.datadoghq",
    "amplitude",
    "mixpanel",
    "segment-analytics",
    "coil-compose",
    "com.github.bumptech.glide",
    "picasso",
)
FORBIDDEN_SOURCE = (
    "android.webkit.WebView",
    "android.text.Html",
    "androidx.compose.ui.viewinterop.AndroidView",
    "com.google.android.gms.ads",
    "com.android.installreferrer",
    "AdvertisingIdClient",
    "AsyncImage",
    "rememberAsyncImagePainter",
)
FORBIDDEN_PERMISSIONS = (
    "com.google.android.gms.permission.AD_ID",
    "android.permission.ACCESS_ADSERVICES_AD_ID",
    "android.permission.ACCESS_ADSERVICES_ATTRIBUTION",
)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(message)


def main() -> None:
    dependency_text = "\n".join(
        (ANDROID / path).read_text()
        for path in ("app/build.gradle.kts", "gradle/libs.versions.toml", "app/gradle.lockfile")
    )
    for marker in FORBIDDEN_DEPENDENCIES:
        require(marker not in dependency_text, f"forbidden ad/tracker dependency: {marker}")

    source_text = "\n".join(path.read_text() for path in MAIN.rglob("*.kt"))
    for marker in FORBIDDEN_SOURCE:
        require(marker not in source_text, f"forbidden executable ad surface: {marker}")

    manifest = ElementTree.parse(MAIN / "AndroidManifest.xml").getroot()
    permissions = [node.attrib[ANDROID_NAME] for node in manifest.findall("uses-permission")]
    require(permissions == ["android.permission.INTERNET"], f"unexpected manifest permissions: {permissions}")
    for permission in FORBIDDEN_PERMISSIONS:
        require(permission not in permissions, f"forbidden advertising permission: {permission}")

    card = (MAIN / "java/net/jim80/podcastreader/ui/ads/HouseAdCard.kt").read_text()
    require("Text(creative.title" in card and "Text(creative.body" in card, "house ads must render as native Text")
    require('Text("Learn more")' in card, "CTA label must remain local and fixed")
    require("private fun HouseAdCard" in card, "generic house-ad rendering must not be a public placement surface")
    require("fun LibraryHouseAdSlot" in card and "fun JobsHouseAdSlot" in card, "only designated Android slots may render")


if __name__ == "__main__":
    main()
