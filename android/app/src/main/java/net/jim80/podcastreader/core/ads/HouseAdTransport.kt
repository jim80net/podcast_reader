package net.jim80.podcastreader.core.ads

import java.io.IOException
import java.io.Reader
import java.time.Instant
import net.jim80.podcastreader.core.premium.PremiumAccessToken
import net.jim80.podcastreader.core.premium.PremiumFailure
import net.jim80.podcastreader.core.premium.PremiumFailureCategory
import net.jim80.podcastreader.core.premium.PremiumFailureMapper
import net.jim80.podcastreader.core.premium.PremiumOrigin
import net.jim80.podcastreader.core.premium.securePremiumHttpClient
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.ResponseBody

class HouseAdRequestFactory(private val origin: PremiumOrigin) {
    fun authenticatedGet(placement: HouseAdPlacement, token: PremiumAccessToken): Request = Request.Builder()
        .url("${origin.value}/v1/ads/inventory/${placement.backendSlot}")
        .header("Authorization", "Bearer ${token.value}")
        .header("Accept", "application/json")
        .get()
        .build()
}

class HouseAdTransport(
    private val requests: HouseAdRequestFactory,
    private val token: PremiumAccessToken,
    private val client: OkHttpClient = securePremiumHttpClient(),
) : HouseInventoryApi {
    override fun fetch(
        placement: HouseAdPlacement,
        now: Instant,
        entitlementValidUntil: Instant,
        requestId: String,
    ): HouseInventoryResult = try {
        client.newCall(requests.authenticatedGet(placement, token)).execute().use { response ->
            if (response.code == 200 || response.code == 204) {
                requirePrivateNoStore(response.header("Cache-Control"))
            }
            when (response.code) {
                200 -> {
                    require(response.body.contentType()?.let { it.type == "application" && it.subtype == "json" } == true) {
                        "unexpected inventory content type"
                    }
                    val dto = houseAdJson.decodeFromString<HouseInventoryV1Dto>(response.body.readBounded())
                    dto.validated(placement, now, entitlementValidUntil).fold(
                        onSuccess = { HouseInventoryResult.Success(it) },
                        onFailure = { incompatible(requestId) },
                    )
                }
                204 -> if (response.body.readBounded().isEmpty()) {
                    HouseInventoryResult.Empty
                } else {
                    incompatible(requestId)
                }
                else -> HouseInventoryResult.Failure(PremiumFailureMapper.fromHttp(response.code, requestId))
            }
        }
    } catch (_: IOException) {
        HouseInventoryResult.Failure(PremiumFailure(PremiumFailureCategory.NETWORK, requestId = requestId))
    } catch (_: RuntimeException) {
        incompatible(requestId)
    }
}

private fun requirePrivateNoStore(value: String?) {
    val directives = value.orEmpty()
        .split(',')
        .map { it.substringBefore('=').trim().lowercase() }
        .toSet()
    require("private" in directives && "no-store" in directives) { "unsafe inventory caching" }
}

private fun incompatible(requestId: String) = HouseInventoryResult.Failure(
    PremiumFailure(PremiumFailureCategory.INCOMPATIBLE_RESPONSE, requestId = requestId),
)

private fun ResponseBody.readBounded(): String {
    val declaredLength = contentLength()
    require(declaredLength == -1L || declaredLength <= MAX_RESPONSE_BYTES) { "response is too large" }
    return charStream().use { it.readAtMost(MAX_RESPONSE_CHARS) }
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
