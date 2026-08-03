package net.jim80.podcastreader.core.premium

import java.io.IOException
import java.io.Reader
import java.time.Duration
import okhttp3.Cache
import okhttp3.CookieJar
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.ResponseBody
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.RequestBody.Companion.toRequestBody

sealed class PremiumRoute protected constructor(internal val path: String) {
    data object CurrentUser : PremiumRoute("/v1/me")
    data object Entitlements : PremiumRoute("/v1/me/entitlements")
    data object DeviceStart : PremiumRoute("/v1/device-authorizations")
    data object DeviceToken : PremiumRoute("/v1/device-authorizations/token")
    data object RefreshToken : PremiumRoute("/v1/tokens/refresh")
    data object RevokeToken : PremiumRoute("/v1/tokens/revoke")
}

internal fun securePremiumHttpClient(): OkHttpClient = OkHttpClient.Builder()
    .followRedirects(false)
    .followSslRedirects(false)
    .cache(null as Cache?)
    .cookieJar(CookieJar.NO_COOKIES)
    .connectTimeout(Duration.ofSeconds(10))
    .callTimeout(Duration.ofSeconds(30))
    .build()

class PremiumRequestFactory(private val origin: PremiumOrigin) {
    fun authenticatedGet(route: PremiumRoute, token: PremiumAccessToken): Request = Request.Builder()
        .url(origin.resolve(route))
        .header("Authorization", "Bearer ${token.value}")
        .header("Accept", "application/json")
        .get()
        .build()

    internal fun deviceStart(): Request = jsonPost(
        PremiumRoute.DeviceStart,
        premiumJson.encodeToString(DeviceAuthorizationRequestV1("android")),
    )

    internal fun devicePoll(deviceCode: DeviceCode): Request = jsonPost(
        PremiumRoute.DeviceToken,
        premiumJson.encodeToString(DeviceTokenRequestV1(deviceCode.authorizationValue())),
    )

    internal fun refresh(refreshToken: PremiumRefreshToken): Request = jsonPost(
        PremiumRoute.RefreshToken,
        premiumJson.encodeToString(RefreshTokenRequestV1(refreshToken.value)),
    )

    internal fun revoke(refreshToken: PremiumRefreshToken): Request = jsonPost(
        PremiumRoute.RevokeToken,
        premiumJson.encodeToString(RefreshTokenRequestV1(refreshToken.value)),
    )

    private fun jsonPost(route: PremiumRoute, json: String): Request = Request.Builder()
        .url(origin.resolve(route))
        .header("Accept", "application/json")
        .post(json.toRequestBody(JSON_MEDIA_TYPE))
        .build()
}

enum class PremiumFailureCategory {
    NETWORK,
    UNAUTHORIZED,
    UNSAFE_ENDPOINT,
    INCOMPATIBLE_RESPONSE,
    SERVER,
}

data class PremiumFailure(
    val category: PremiumFailureCategory,
    val httpStatus: Int? = null,
    val requestId: String,
)

sealed interface EntitlementFetchResult {
    data class Success(val entitlement: EntitlementV1Dto) : EntitlementFetchResult

    data class Failure(val failure: PremiumFailure) : EntitlementFetchResult
}

sealed interface CurrentUserFetchResult {
    data class Success(val subject: String) : CurrentUserFetchResult {
        override fun toString(): String = "CurrentUserFetchResult.Success(redacted)"
    }

    data class Failure(val failure: PremiumFailure) : CurrentUserFetchResult
}

interface PremiumCurrentUserApi {
    fun fetch(token: PremiumAccessToken, requestId: String): CurrentUserFetchResult
}

class PremiumCurrentUserTransport(
    private val requestFactory: PremiumRequestFactory,
    private val client: OkHttpClient = securePremiumHttpClient(),
) : PremiumCurrentUserApi {
    override fun fetch(token: PremiumAccessToken, requestId: String): CurrentUserFetchResult = try {
        client.newCall(requestFactory.authenticatedGet(PremiumRoute.CurrentUser, token)).execute().use { response ->
            if (response.code != 200) {
                CurrentUserFetchResult.Failure(PremiumFailureMapper.fromHttp(response.code, requestId))
            } else {
                val body = requireNotNull(response.body) { "missing response" }
                premiumJson.decodeFromString<CurrentUserV1Dto>(body.readBounded())
                    .validatedSubject()
                    .fold(
                        onSuccess = { CurrentUserFetchResult.Success(it) },
                        onFailure = { incompatibleCurrentUser(requestId) },
                    )
            }
        }
    } catch (_: IOException) {
        CurrentUserFetchResult.Failure(
            PremiumFailure(PremiumFailureCategory.NETWORK, requestId = requestId),
        )
    } catch (_: RuntimeException) {
        incompatibleCurrentUser(requestId)
    }
}

interface PremiumEntitlementApi {
    fun fetch(token: PremiumAccessToken, requestId: String): EntitlementFetchResult
}

class PremiumEntitlementTransport(
    private val requestFactory: PremiumRequestFactory,
    private val client: OkHttpClient = securePremiumHttpClient(),
) : PremiumEntitlementApi {
    override fun fetch(token: PremiumAccessToken, requestId: String): EntitlementFetchResult = try {
        client.newCall(requestFactory.authenticatedGet(PremiumRoute.Entitlements, token)).execute().use { response ->
            if (response.code != 200) {
                EntitlementFetchResult.Failure(PremiumFailureMapper.fromHttp(response.code, requestId))
            } else {
                val body = requireNotNull(response.body) { "missing response" }
                val decoded = premiumJson.decodeFromString<EntitlementV1Dto>(body.readBounded())
                EntitlementFetchResult.Success(decoded)
            }
        }
    } catch (_: IOException) {
        EntitlementFetchResult.Failure(
            PremiumFailure(PremiumFailureCategory.NETWORK, requestId = requestId),
        )
    } catch (_: RuntimeException) {
        EntitlementFetchResult.Failure(
            PremiumFailure(PremiumFailureCategory.INCOMPATIBLE_RESPONSE, requestId = requestId),
        )
    }
}

object PremiumFailureMapper {
    fun fromHttp(status: Int, requestId: String): PremiumFailure = PremiumFailure(
        category = when (status) {
            401 -> PremiumFailureCategory.UNAUTHORIZED
            in 300..399 -> PremiumFailureCategory.UNSAFE_ENDPOINT
            in 500..599 -> PremiumFailureCategory.SERVER
            else -> PremiumFailureCategory.INCOMPATIBLE_RESPONSE
        },
        httpStatus = status,
        requestId = requestId,
    )
}

private fun incompatibleCurrentUser(requestId: String) = CurrentUserFetchResult.Failure(
    PremiumFailure(PremiumFailureCategory.INCOMPATIBLE_RESPONSE, requestId = requestId),
)

internal fun ResponseBody.readBounded(): String {
    val declaredLength = contentLength()
    require(declaredLength == -1L || declaredLength <= MAX_RESPONSE_BYTES) { "response is too large" }
    return charStream().use { reader -> reader.readAtMost(MAX_RESPONSE_CHARS) }
}

private fun Reader.readAtMost(limit: Int): String {
    val buffer = CharArray(4_096)
    val result = StringBuilder()
    while (true) {
        val count = read(buffer)
        if (count == -1) return result.toString()
        require(result.length + count <= limit) { "response is too large" }
        result.append(buffer, 0, count)
    }
}

private const val MAX_RESPONSE_BYTES = 64L * 1024L
private const val MAX_RESPONSE_CHARS = 64 * 1024
private val JSON_MEDIA_TYPE = "application/json; charset=utf-8".toMediaType()
