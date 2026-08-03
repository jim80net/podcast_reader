package net.jim80.podcastreader.acceptance

internal object AcceptanceSecrets {
    private data class Marker(val name: String, val full: String, val prefix: String)

    // This is the only marker manifest. The post-device sweep parses these
    // structured entries with extract_android_k4_markers.py and fails closed
    // if a definition cannot be understood.
    private val definitions = listOf(
        Marker(name = "ENGINE_BEARER", full = "K4_ENGINE_BEARER_7f4d1a9c2e6b", prefix = "K4_ENGINE_BEARER_"),
        Marker(name = "DEVICE_CODE", full = "K4_DEVICE_CODE_9b3e7c1a5d8f", prefix = "K4_DEVICE_CODE_"),
        Marker(name = "PREMIUM_ACCESS", full = "K4_PREMIUM_ACCESS_2c8e4a6f1d7b", prefix = "K4_PREMIUM_ACCESS_"),
        Marker(name = "PREMIUM_REFRESH", full = "K4_PREMIUM_REFRESH_6d1f9a3c7e2b", prefix = "K4_PREMIUM_REFRESH_"),
    )

    val ENGINE_BEARER = marker("ENGINE_BEARER").full
    val DEVICE_CODE = marker("DEVICE_CODE").full
    val PREMIUM_ACCESS = marker("PREMIUM_ACCESS").full
    val PREMIUM_REFRESH = marker("PREMIUM_REFRESH").full
    val fullAndPrefixMarkers = definitions.flatMap { listOf(it.full, it.prefix) }

    private fun marker(name: String): Marker = definitions.single { it.name == name }
}
