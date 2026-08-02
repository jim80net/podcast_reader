package net.jim80.podcastreader.core.engine

import java.io.IOException
import java.time.Duration
import okhttp3.Cache
import okhttp3.CookieJar
import okhttp3.OkHttpClient
import okhttp3.Request

class EngineBearer private constructor(internal val value: String) {
    override fun toString(): String = "EngineBearer(redacted)"

    companion object {
        fun fromClaim(value: String): Result<EngineBearer> = runCatching {
            require(value.isNotBlank() && value.length <= 4096) { "invalid credential" }
            EngineBearer(value)
        }
    }
}

internal fun secureEngineHttpClient(): OkHttpClient = OkHttpClient.Builder()
    .followRedirects(false)
    .followSslRedirects(false)
    .cache(null as Cache?)
    .cookieJar(CookieJar.NO_COOKIES)
    .connectTimeout(Duration.ofSeconds(10))
    .callTimeout(Duration.ofSeconds(30))
    .build()

class EngineRequestFactory(private val origin: TailnetOrigin) {
    fun authenticatedGet(route: EngineRoute, bearer: EngineBearer): Request = Request.Builder()
        .url(origin.resolve(route))
        .header("Authorization", "Bearer ${bearer.value}")
        .header("Accept", "application/json")
        .get()
        .build()
}

enum class EngineFailureCategory {
    NETWORK,
    UNAUTHORIZED,
    UNSAFE_ENDPOINT,
    INCOMPATIBLE_RESPONSE,
    SERVER,
}

data class EngineFailure(
    val category: EngineFailureCategory,
    val httpStatus: Int? = null,
    val requestId: String,
)

object EngineFailureMapper {
    fun fromHttp(status: Int, requestId: String): EngineFailure = EngineFailure(
        category = when (status) {
            401 -> EngineFailureCategory.UNAUTHORIZED
            in 300..399 -> EngineFailureCategory.UNSAFE_ENDPOINT
            in 500..599 -> EngineFailureCategory.SERVER
            else -> EngineFailureCategory.INCOMPATIBLE_RESPONSE
        },
        httpStatus = status,
        requestId = requestId,
    )

    fun fromIo(@Suppress("UNUSED_PARAMETER") failure: IOException, requestId: String): EngineFailure =
        EngineFailure(EngineFailureCategory.NETWORK, requestId = requestId)
}
