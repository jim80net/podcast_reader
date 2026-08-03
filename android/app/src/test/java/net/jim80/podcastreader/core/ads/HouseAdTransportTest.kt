package net.jim80.podcastreader.core.ads

import java.time.Instant
import net.jim80.podcastreader.core.engine.EngineBearer
import net.jim80.podcastreader.core.premium.PremiumAccessToken
import net.jim80.podcastreader.core.premium.PremiumFailureCategory
import net.jim80.podcastreader.core.premium.PremiumOrigin
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Protocol
import okhttp3.Response
import okhttp3.ResponseBody.Companion.toResponseBody
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class HouseAdTransportTest {
    private val origin = PremiumOrigin.fromTrustedConfiguration("https://premium.test").getOrThrow()
    private val token = PremiumAccessToken.fromAuthorization("premium_access_token_abcdefghijklmnopqrstuvwxyz").getOrThrow()
    private val now = Instant.parse("2026-08-03T00:00:00Z")
    private val validUntil = Instant.parse("2026-08-03T00:05:00Z")

    @Test
    fun requestCarriesOnlyPremiumBearerAndFixedSlotContext() {
        val request = HouseAdRequestFactory(origin).authenticatedGet(HouseAdPlacement.JOBS, token)

        assertEquals("https://premium.test/v1/ads/inventory/mobile_home", request.url.toString())
        assertEquals("Bearer premium_access_token_abcdefghijklmnopqrstuvwxyz", request.header("Authorization"))
        assertNull(request.url.query)
        val parameterTypes = HouseAdRequestFactory::class.java.methods.flatMap { it.parameterTypes.asList() }
        assertTrue(parameterTypes.contains(PremiumAccessToken::class.java))
        assertFalse(parameterTypes.contains(EngineBearer::class.java))
    }

    @Test
    fun transportConsumesNoStoreFixtureAndEmpty204() {
        val success = transport(200, fixture("eligible-library.json"), "private, no-store")
            .fetch(HouseAdPlacement.LIBRARY, now, validUntil, "r1")
        assertTrue(success is HouseInventoryResult.Success)
        assertTrue(
            transport(204, "", "private, no-store")
                .fetch(HouseAdPlacement.LIBRARY, now, validUntil, "r2") is HouseInventoryResult.Empty,
        )
    }

    @Test
    fun unsafeHeadersRedirectAndMalformedPayloadFailClosed() {
        val noStore = transport(200, fixture("eligible-library.json"), "private")
            .fetch(HouseAdPlacement.LIBRARY, now, validUntil, "r1") as HouseInventoryResult.Failure
        val redirect = transport(302, "")
            .fetch(HouseAdPlacement.LIBRARY, now, validUntil, "r2") as HouseInventoryResult.Failure
        val malformed = transport(200, fixture("malformed.json"), "private, no-store")
            .fetch(HouseAdPlacement.LIBRARY, now, validUntil, "r3") as HouseInventoryResult.Failure
        val misleadingCache = transport(200, fixture("eligible-library.json"), "not-private, no-store")
            .fetch(HouseAdPlacement.LIBRARY, now, validUntil, "r4") as HouseInventoryResult.Failure
        val wrongContentType = transport(
            200,
            fixture("eligible-library.json"),
            "private, no-store",
            "text/html".toMediaType(),
        ).fetch(HouseAdPlacement.LIBRARY, now, validUntil, "r5") as HouseInventoryResult.Failure
        val cacheableEmpty = transport(204, "")
            .fetch(HouseAdPlacement.LIBRARY, now, validUntil, "r6") as HouseInventoryResult.Failure

        assertEquals(PremiumFailureCategory.INCOMPATIBLE_RESPONSE, noStore.failure.category)
        assertEquals(PremiumFailureCategory.UNSAFE_ENDPOINT, redirect.failure.category)
        assertEquals(PremiumFailureCategory.INCOMPATIBLE_RESPONSE, malformed.failure.category)
        assertEquals(PremiumFailureCategory.INCOMPATIBLE_RESPONSE, misleadingCache.failure.category)
        assertEquals(PremiumFailureCategory.INCOMPATIBLE_RESPONSE, wrongContentType.failure.category)
        assertEquals(PremiumFailureCategory.INCOMPATIBLE_RESPONSE, cacheableEmpty.failure.category)
    }

    private fun transport(
        status: Int,
        body: String,
        cacheControl: String? = null,
        contentType: okhttp3.MediaType = "application/json".toMediaType(),
    ): HouseAdTransport {
        val client = OkHttpClient.Builder().addInterceptor { chain ->
            Response.Builder()
                .request(chain.request())
                .protocol(Protocol.HTTP_1_1)
                .code(status)
                .message("fixture")
                .apply { if (cacheControl != null) header("Cache-Control", cacheControl) }
                .body(body.toResponseBody(contentType))
                .build()
        }.build()
        return HouseAdTransport(HouseAdRequestFactory(origin), token, client)
    }

    private fun fixture(name: String): String = requireNotNull(
        javaClass.classLoader?.getResource("v1/ads/$name"),
    ).readText()
}
