package net.jim80.podcastreader.core.premium

import java.time.Instant
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class DeviceAuthorizationFlowTest {
    private val origin = PremiumOrigin.fromTrustedConfiguration("https://premium.test").getOrThrow()
    private val now = Instant.parse("2026-08-02T00:00:00Z")

    @Test
    fun beginsInSystemBrowserThenRefusesEarlyPolling() {
        val api = FakeNativeAuthApi(startResult = NativeAuthResult.Success(startDto()))
        val opened = mutableListOf<String>()
        val flow = DeviceAuthorizationFlow(origin, api) { uri -> Result.success(Unit).also { opened += uri } }
        val waiting = flow.begin(now, "r1") as DeviceAuthorizationTransition.Waiting

        assertEquals(listOf("https://premium.test/device"), opened)
        assertTrue(flow.poll(waiting.session, now.plusSeconds(4), "r2") is DeviceAuthorizationTransition.TooEarly)
        assertEquals(0, api.pollCount)
    }

    @Test
    fun pendingKeepsIntervalAndSlowDownAddsFiveSeconds() {
        val api = FakeNativeAuthApi(startResult = NativeAuthResult.Success(startDto()))
        val flow = DeviceAuthorizationFlow(origin, api) { Result.success(Unit) }
        val initial = (flow.begin(now, "r1") as DeviceAuthorizationTransition.Waiting).session
        api.pollResult = protocol(NativeAuthErrorCode.AUTHORIZATION_PENDING)
        val pending = flow.poll(initial, now.plusSeconds(5), "r2") as DeviceAuthorizationTransition.Waiting
        api.pollResult = protocol(NativeAuthErrorCode.SLOW_DOWN)
        val slowed = flow.poll(pending.session, pending.session.nextPollAt, "r3") as DeviceAuthorizationTransition.Waiting

        assertEquals(5, pending.session.pollIntervalSeconds)
        assertEquals(10, slowed.session.pollIntervalSeconds)
        assertEquals(2, api.pollCount)
    }

    @Test
    fun successAndTerminalErrorsAreBoundedAndRedacted() {
        val api = FakeNativeAuthApi(startResult = NativeAuthResult.Success(startDto()))
        val flow = DeviceAuthorizationFlow(origin, api) { Result.success(Unit) }
        val session = (flow.begin(now, "r1") as DeviceAuthorizationTransition.Waiting).session
        api.pollResult = NativeAuthResult.Success(tokenDto())
        val authorized = flow.poll(session, session.nextPollAt, "r2") as DeviceAuthorizationTransition.Authorized

        assertFalse(authorized.toString().contains(tokenDto().refreshToken))
        api.pollResult = protocol(NativeAuthErrorCode.ACCESS_DENIED)
        assertTrue(flow.poll(session, session.nextPollAt, "r3") is DeviceAuthorizationTransition.Denied)
        assertTrue(flow.poll(session, session.expiresAt, "r4") is DeviceAuthorizationTransition.Expired)
    }

    @Test
    fun cancellationClearsThePendingDeviceSecretBeforeAnyFurtherRequest() {
        val api = FakeNativeAuthApi(startResult = NativeAuthResult.Success(startDto()))
        val flow = DeviceAuthorizationFlow(origin, api) { Result.success(Unit) }
        val session = (flow.begin(now, "r1") as DeviceAuthorizationTransition.Waiting).session

        assertTrue(flow.cancel(session) is DeviceAuthorizationTransition.Cancelled)
        assertTrue(runCatching { PremiumRequestFactory(origin).devicePoll(session.deviceCode) }.isFailure)
        assertEquals(0, api.pollCount)
    }

    private fun startDto() = DeviceAuthorizationStartV1Dto(
        "fixture_device_code_abcdefghijklmnopqrstuvwxyz", "ABCD-EFGH", "https://premium.test/device", 600, 5,
    )

    private fun tokenDto() = TokenResponseV1Dto(
        "fixture_access_token_abcdefghijklmnopqrstuvwxyz", "Bearer", 900,
        "fixture_refresh_token_abcdefghijklmnopqrstuvwxyz",
    )

    private fun protocol(code: NativeAuthErrorCode) = NativeAuthResult.ProtocolError(
        NativeAuthErrorV1Dto(code, "bounded", "request-1"),
    )
}

private class FakeNativeAuthApi(
    private val startResult: NativeAuthResult<DeviceAuthorizationStartV1Dto>,
) : PremiumNativeAuthApi {
    var pollResult: NativeAuthResult<TokenResponseV1Dto> = NativeAuthResult.Failure(
        PremiumFailure(PremiumFailureCategory.NETWORK, requestId = "unset"),
    )
    var pollCount = 0

    override fun start(requestId: String) = startResult
    override fun poll(deviceCode: DeviceCode, requestId: String): NativeAuthResult<TokenResponseV1Dto> =
        pollResult.also { pollCount += 1 }
    override fun refresh(refreshToken: PremiumRefreshToken, requestId: String): NativeAuthResult<TokenResponseV1Dto> = error("unused")
    override fun revoke(refreshToken: PremiumRefreshToken, requestId: String): NativeAuthResult<Unit> = error("unused")
}
