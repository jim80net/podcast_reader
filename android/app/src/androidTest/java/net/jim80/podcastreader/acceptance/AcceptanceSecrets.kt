package net.jim80.podcastreader.acceptance

internal object AcceptanceSecrets {
    const val ENGINE_BEARER = "K4_ENGINE_BEARER_7f4d1a9c2e6b"
    const val ENGINE_PREFIX = "K4_ENGINE_BEARER_"
    const val DEVICE_CODE = "K4_DEVICE_CODE_9b3e7c1a5d8f"
    const val DEVICE_PREFIX = "K4_DEVICE_CODE_"
    const val PREMIUM_ACCESS = "K4_PREMIUM_ACCESS_2c8e4a6f1d7b"
    const val ACCESS_PREFIX = "K4_PREMIUM_ACCESS_"
    const val PREMIUM_REFRESH = "K4_PREMIUM_REFRESH_6d1f9a3c7e2b"
    const val REFRESH_PREFIX = "K4_PREMIUM_REFRESH_"

    val fullAndPrefixMarkers = listOf(
        ENGINE_BEARER,
        ENGINE_PREFIX,
        DEVICE_CODE,
        DEVICE_PREFIX,
        PREMIUM_ACCESS,
        ACCESS_PREFIX,
        PREMIUM_REFRESH,
        REFRESH_PREFIX,
    )
}
