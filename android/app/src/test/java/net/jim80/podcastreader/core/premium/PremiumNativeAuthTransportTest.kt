package net.jim80.podcastreader.core.premium

import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Protocol
import okhttp3.Response
import okhttp3.ResponseBody.Companion.toResponseBody
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class PremiumNativeAuthTransportTest {
    private val origin = PremiumOrigin.fromTrustedConfiguration("https://premium.test").getOrThrow()
    private val refresh = PremiumRefreshToken.fromAuthorization(
        "fixture_refresh_token_abcdefghijklmnopqrstuvwxyz",
    ).getOrThrow()

    @Test
    fun frozenRefreshReuseEnvelopeIsDecodedEvenOn401() {
        val api = transport(
            401,
            """{"code":"refresh_token_reused","message":"The token family has been revoked","request_id":"req_fixture"}""",
        )

        val result = api.refresh(refresh, "local-request") as NativeAuthResult.ProtocolError
        assertEquals(NativeAuthErrorCode.REFRESH_TOKEN_REUSED, result.error.code)
    }

    @Test
    fun revokeAcceptsOnlyAnEmpty204() {
        assertTrue(transport(204, "").revoke(refresh, "r1") is NativeAuthResult.Success)
        val bodyOn204 = transport(204, "unexpected").revoke(refresh, "r2") as NativeAuthResult.Failure
        assertEquals(PremiumFailureCategory.INCOMPATIBLE_RESPONSE, bodyOn204.failure.category)
    }

    @Test
    fun unfrozen401CodeBecomesGenericUnauthorizedWithoutInventingADtoValue() {
        val result = transport(
            401,
            """{"code":"refresh_token_invalid","message":"invalid","request_id":"req_fixture"}""",
        ).refresh(refresh, "local-request") as NativeAuthResult.Failure

        assertEquals(PremiumFailureCategory.UNAUTHORIZED, result.failure.category)
    }

    private fun transport(status: Int, body: String): PremiumNativeAuthTransport {
        val client = OkHttpClient.Builder().addInterceptor { chain ->
            Response.Builder()
                .request(chain.request())
                .protocol(Protocol.HTTP_1_1)
                .code(status)
                .message("fixture")
                .body(body.toResponseBody("application/json".toMediaType()))
                .build()
        }.build()
        return PremiumNativeAuthTransport(PremiumRequestFactory(origin), client)
    }
}
