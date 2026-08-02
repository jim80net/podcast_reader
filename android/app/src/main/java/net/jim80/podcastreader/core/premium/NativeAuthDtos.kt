package net.jim80.podcastreader.core.premium

import java.net.URI
import java.time.Instant
import java.util.Arrays
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

@Serializable
data class DeviceAuthorizationStartV1Dto(
    @SerialName("device_code") val deviceCode: String,
    @SerialName("user_code") val userCode: String,
    @SerialName("verification_uri") val verificationUri: String,
    @SerialName("expires_in") val expiresIn: Long,
    val interval: Long,
) {
    override fun toString(): String = "DeviceAuthorizationStartV1Dto(redacted)"

    internal fun validated(origin: PremiumOrigin, receivedAt: Instant): Result<DeviceAuthorizationSession> = runCatching {
        val secret = DeviceCode.fromAuthorization(deviceCode).getOrThrow()
        val displayCode = UserCode.fromAuthorization(userCode).getOrThrow()
        require(expiresIn > 0 && interval > 0) { "invalid device authorization lifetime" }
        val uri = URI(verificationUri)
        require(uri.scheme == "https" && uri.rawUserInfo == null && uri.rawFragment == null) {
            "unsafe verification URI"
        }
        require(uri.rawQuery == null && uri.rawPath == "/device") { "unexpected verification URI" }
        require("${uri.scheme}://${uri.rawAuthority}" == origin.value) { "verification origin mismatch" }
        DeviceAuthorizationSession(
            origin = origin,
            deviceCode = secret,
            userCode = displayCode,
            verificationUri = verificationUri,
            expiresAt = receivedAt.plusSeconds(expiresIn),
            pollIntervalSeconds = interval,
            nextPollAt = receivedAt.plusSeconds(interval),
        )
    }
}

@Serializable
data class TokenResponseV1Dto(
    @SerialName("access_token") val accessToken: String,
    @SerialName("token_type") val tokenType: String,
    @SerialName("expires_in") val expiresIn: Long,
    @SerialName("refresh_token") val refreshToken: String,
) {
    override fun toString(): String = "TokenResponseV1Dto(redacted)"

    internal fun validated(): Result<AuthorizedPremiumTokens> = runCatching {
        require(tokenType == "Bearer" && expiresIn > 0) { "invalid token response" }
        AuthorizedPremiumTokens(
            accessToken = PremiumAccessToken.fromAuthorization(accessToken).getOrThrow(),
            refreshToken = PremiumRefreshToken.fromAuthorization(refreshToken).getOrThrow(),
        )
    }
}

@Serializable
data class NativeAuthErrorV1Dto(
    val code: NativeAuthErrorCode,
    val message: String,
    @SerialName("request_id") val requestId: String,
) {
    override fun toString(): String = "NativeAuthErrorV1Dto(code=$code, requestId=redacted)"

    internal fun requireValid(): NativeAuthErrorV1Dto = apply {
        require(message.length in 1..200 && requestId.length in 1..64) { "invalid error envelope" }
    }
}

@Serializable
enum class NativeAuthErrorCode {
    @SerialName("authorization_pending") AUTHORIZATION_PENDING,
    @SerialName("slow_down") SLOW_DOWN,
    @SerialName("expired_token") EXPIRED_TOKEN,
    @SerialName("access_denied") ACCESS_DENIED,
    @SerialName("refresh_token_reused") REFRESH_TOKEN_REUSED,
}

@Serializable
internal data class DeviceAuthorizationRequestV1(val client: String)

@Serializable
internal data class DeviceTokenRequestV1(@SerialName("device_code") val deviceCode: String)

@Serializable
internal data class RefreshTokenRequestV1(@SerialName("refresh_token") val refreshToken: String)

@Serializable
internal data class RevokeFixtureV1(val status: Int, val body: Nothing?)

data class AuthorizedPremiumTokens(
    val accessToken: PremiumAccessToken,
    val refreshToken: PremiumRefreshToken,
) {
    override fun toString(): String = "AuthorizedPremiumTokens(redacted)"
}

class DeviceCode private constructor(private val value: CharArray) {
    private var cleared = false

    override fun toString(): String = "DeviceCode(redacted)"

    @Synchronized
    internal fun authorizationValue(): String {
        require(!cleared) { "device code is cleared" }
        return value.concatToString()
    }

    @Synchronized
    internal fun clear() {
        Arrays.fill(value, '\u0000')
        cleared = true
    }

    companion object {
        fun fromAuthorization(value: String): Result<DeviceCode> = runCatching {
            require(value.length in 20..256 && value.none(Char::isWhitespace)) { "invalid device code" }
            DeviceCode(value.toCharArray())
        }
    }
}

class UserCode private constructor(val value: String) {
    override fun toString(): String = "UserCode(redacted)"

    companion object {
        private val FORMAT = Regex("^[23456789ABCDEFGHJKLMNPQRSTUVWXYZ]{4}-[23456789ABCDEFGHJKLMNPQRSTUVWXYZ]{4}$")

        fun fromAuthorization(value: String): Result<UserCode> = runCatching {
            require(FORMAT.matches(value)) { "invalid user code" }
            UserCode(value)
        }
    }
}
