package net.jim80.podcastreader.core.premium

import java.io.IOException
import java.io.Reader
import java.time.Duration
import okhttp3.Cache
import okhttp3.CookieJar
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.ResponseBody

sealed class PremiumRoute protected constructor(internal val path: String) {
    data object Entitlements : PremiumRoute("/v1/me/entitlements")
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

class PremiumEntitlementTransport(
    private val requestFactory: PremiumRequestFactory,
    private val client: OkHttpClient = securePremiumHttpClient(),
) {
    fun fetch(token: PremiumAccessToken, requestId: String): EntitlementFetchResult = try {
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

private fun ResponseBody.readBounded(): String {
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
