package net.jim80.podcastreader.core.premium

import kotlinx.serialization.builtins.ListSerializer
import net.jim80.podcastreader.core.engine.EngineBearer
import okio.Buffer
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class NativeAuthContractTest {
    private val origin = PremiumOrigin.fromTrustedConfiguration("https://premium.test").getOrThrow()

    @Test
    fun committedDeviceAndTokenFixturesDecodeStrictly() {
        val start = premiumJson.decodeFromString<DeviceAuthorizationStartV1Dto>(fixture("native-auth-v1-device-start.json"))
        val token = premiumJson.decodeFromString<TokenResponseV1Dto>(fixture("native-auth-v1-token-response.json"))

        assertTrue(start.validated(origin, java.time.Instant.EPOCH).isSuccess)
        assertTrue(token.validated().isSuccess)
        assertFalse(start.toString().contains(start.deviceCode))
        assertFalse(token.toString().contains(token.refreshToken))
    }

    @Test
    fun allFiveFrozenErrorsDecodeAndNoOthersAreAccepted() {
        val errors = premiumJson.decodeFromString(
            ListSerializer(NativeAuthErrorV1Dto.serializer()),
            fixture("native-auth-v1-errors.json"),
        )

        assertEquals(NativeAuthErrorCode.entries, errors.map { it.requireValid().code })
        assertTrue(runCatching {
            premiumJson.decodeFromString<NativeAuthErrorV1Dto>(
                """{"code":"new_server_error","message":"x","request_id":"r"}""",
            )
        }.isFailure)
    }

    @Test
    fun requestBodiesAndRoutesExactlyConsumeTheFrozenContract() {
        val factory = PremiumRequestFactory(origin)
        val deviceCode = DeviceCode.fromAuthorization("fixture_device_code_abcdefghijklmnopqrstuvwxyz").getOrThrow()
        val refresh = PremiumRefreshToken.fromAuthorization("fixture_refresh_token_abcdefghijklmnopqrstuvwxyz").getOrThrow()
        val start = factory.deviceStart()
        val poll = factory.devicePoll(deviceCode)
        val refreshRequest = factory.refresh(refresh)
        val revoke = factory.revoke(refresh)

        assertEquals("https://premium.test/v1/device-authorizations", start.url.toString())
        assertEquals("{\"client\":\"android\"}", start.bodyJson())
        assertEquals("https://premium.test/v1/device-authorizations/token", poll.url.toString())
        assertEquals("{\"device_code\":\"fixture_device_code_abcdefghijklmnopqrstuvwxyz\"}", poll.bodyJson())
        assertEquals("https://premium.test/v1/tokens/refresh", refreshRequest.url.toString())
        assertEquals(
            premiumJson.parseToJsonElement(fixture("native-auth-v1-revoke-request.json")),
            premiumJson.parseToJsonElement(revoke.bodyJson()),
        )
        listOf(start, poll, refreshRequest, revoke).forEach { assertNull(it.header("Authorization")) }
    }

    @Test
    fun revokeResponseFixtureRequiresAnEmpty204() {
        val response = premiumJson.decodeFromString<RevokeFixtureV1>(fixture("native-auth-v1-revoke-response.json"))
        assertEquals(204, response.status)
        assertNull(response.body)
    }

    @Test
    fun verificationUriCannotEscapeTheConfiguredPremiumOrigin() {
        val fixture = premiumJson.decodeFromString<DeviceAuthorizationStartV1Dto>(fixture("native-auth-v1-device-start.json"))
        assertTrue(fixture.copy(verificationUri = "https://attacker.example/device").validated(origin, java.time.Instant.EPOCH).isFailure)
        assertTrue(fixture.copy(verificationUri = "https://premium.test/device?code=leak").validated(origin, java.time.Instant.EPOCH).isFailure)
    }

    @Test
    fun nativeAuthRequestSurfaceCannotAcceptTheHomeEngineTokenType() {
        val parameterTypes = PremiumRequestFactory::class.java.declaredMethods.flatMap { it.parameterTypes.asList() }
        assertFalse(parameterTypes.contains(EngineBearer::class.java))
        assertTrue(parameterTypes.contains(PremiumRefreshToken::class.java))
        assertTrue(parameterTypes.contains(DeviceCode::class.java))
    }

    private fun fixture(name: String): String = requireNotNull(javaClass.classLoader?.getResource(name)).readText()

    private fun okhttp3.Request.bodyJson(): String = Buffer().also { requireNotNull(body).writeTo(it) }.readUtf8()
}
