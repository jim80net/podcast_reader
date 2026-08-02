package net.jim80.podcastreader.core.premium

class PremiumAccessToken private constructor(internal val value: String) {
    override fun toString(): String = "PremiumAccessToken(redacted)"

    companion object {
        fun fromAuthorization(value: String): Result<PremiumAccessToken> = runCatching {
            require(value.length in 20..256 && value.none(Char::isWhitespace)) { "invalid credential" }
            PremiumAccessToken(value)
        }
    }
}

class PremiumRefreshToken private constructor(internal val value: String) {
    override fun toString(): String = "PremiumRefreshToken(redacted)"

    companion object {
        fun fromAuthorization(value: String): Result<PremiumRefreshToken> = runCatching {
            require(value.length in 20..256 && value.none(Char::isWhitespace)) { "invalid credential" }
            PremiumRefreshToken(value)
        }
    }
}

data class PremiumAccountCredentials(
    val origin: PremiumOrigin,
    val refreshToken: PremiumRefreshToken,
) {
    override fun toString(): String = "PremiumAccountCredentials(redacted)"
}
