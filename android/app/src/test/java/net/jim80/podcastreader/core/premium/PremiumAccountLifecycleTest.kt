package net.jim80.podcastreader.core.premium

import net.jim80.podcastreader.core.security.FakeEncryptedRecordBackend
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class PremiumAccountLifecycleTest {
    private val origin = PremiumOrigin.fromTrustedConfiguration("https://premium.test").getOrThrow()
    private val oldAccess = access("old_access_token_abcdefghijklmnopqrstuvwxyz")
    private val oldRefresh = refresh("old_refresh_token_abcdefghijklmnopqrstuvwxyz")

    @Test
    fun unauthorizedEntitlementRefreshesAndRetriesExactlyOnce() {
        val authorizer = authorizer()
        install(authorizer)
        val native = LifecycleApi(refreshResult = NativeAuthResult.Success(tokenDto()))
        val entitlements = SequenceEntitlementApi(
            mutableListOf(unauthorized(), unauthorized()),
        )

        val result = PremiumEntitlementSession(authorizer, entitlements, native).fetch("request-1")

        assertTrue(result is EntitlementFetchResult.Failure)
        assertEquals(1, native.refreshCount)
        assertEquals(2, entitlements.tokens.size)
        assertEquals("new_access_token_abcdefghijklmnopqrstuvwxyz", entitlements.tokens.last().value)
        assertEquals("new_refresh_token_abcdefghijklmnopqrstuvwxyz", authorizer.restoreAccountRecord().getOrThrow()?.refreshToken?.value)
    }

    @Test
    fun reusedRefreshTokenDisconnectsThePremiumRecord() {
        val authorizer = authorizer()
        install(authorizer)
        val native = LifecycleApi(
            refreshResult = NativeAuthResult.ProtocolError(
                NativeAuthErrorV1Dto(NativeAuthErrorCode.REFRESH_TOKEN_REUSED, "revoked", "request-2"),
            ),
        )

        assertTrue(authorizer.refreshOnce(native, "request-2") is SessionMutationResult.Disconnected)
        assertNull(authorizer.currentAccessToken())
        assertNull(authorizer.restoreAccountRecord().getOrThrow())
    }

    @Test
    fun genericUnauthorizedRefreshAlsoRequiresAReconnect() {
        val authorizer = authorizer()
        install(authorizer)
        val native = LifecycleApi(
            refreshResult = NativeAuthResult.Failure(
                PremiumFailure(PremiumFailureCategory.UNAUTHORIZED, 401, "request-2b"),
            ),
        )

        assertTrue(authorizer.refreshOnce(native, "request-2b") is SessionMutationResult.Disconnected)
        assertNull(authorizer.restoreAccountRecord().getOrThrow())
    }

    @Test
    fun networkRefreshFailureAlsoReturnsToLocalMode() {
        val authorizer = authorizer()
        install(authorizer)
        val offline = LifecycleApi(
            refreshResult = NativeAuthResult.Failure(
                PremiumFailure(PremiumFailureCategory.NETWORK, requestId = "request-2c"),
            ),
        )

        assertTrue(authorizer.refreshOnce(offline, "request-2c") is SessionMutationResult.Disconnected)
        assertNull(authorizer.currentAccessToken())
        assertNull(authorizer.restoreAccountRecord().getOrThrow())
    }

    @Test
    fun revokeAttemptsTheServerThenClearsEvenWhenOffline() {
        val authorizer = authorizer()
        install(authorizer)
        val offline = LifecycleApi(
            revokeResult = NativeAuthResult.Failure(
                PremiumFailure(PremiumFailureCategory.NETWORK, requestId = "request-3"),
            ),
        )

        assertTrue(authorizer.revoke(offline, "request-3") is SessionMutationResult.Disconnected)
        assertNull(authorizer.currentAccessToken())
        assertNull(authorizer.restoreAccountRecord().getOrThrow())
        assertEquals(1, offline.revokeCount)
    }

    private fun authorizer() = PremiumAccountAuthorizer(
        PremiumCredentialStore(FakeEncryptedRecordBackend(PremiumCredentialStore.storageIdentity)),
    )

    private fun install(authorizer: PremiumAccountAuthorizer) {
        authorizer.installAuthorizedSession(PremiumAccountCredentials(origin, oldRefresh), oldAccess).getOrThrow()
    }

    private fun tokenDto() = TokenResponseV1Dto(
        "new_access_token_abcdefghijklmnopqrstuvwxyz", "Bearer", 900,
        "new_refresh_token_abcdefghijklmnopqrstuvwxyz",
    )

    private fun unauthorized() = EntitlementFetchResult.Failure(
        PremiumFailure(PremiumFailureCategory.UNAUTHORIZED, 401, "request"),
    )

    private fun access(value: String) = PremiumAccessToken.fromAuthorization(value).getOrThrow()
    private fun refresh(value: String) = PremiumRefreshToken.fromAuthorization(value).getOrThrow()
}

private class SequenceEntitlementApi(
    private val results: MutableList<EntitlementFetchResult>,
) : PremiumEntitlementApi {
    val tokens = mutableListOf<PremiumAccessToken>()

    override fun fetch(token: PremiumAccessToken, requestId: String): EntitlementFetchResult {
        tokens += token
        return results.removeAt(0)
    }
}

private class LifecycleApi(
    private val refreshResult: NativeAuthResult<TokenResponseV1Dto> = NativeAuthResult.Failure(
        PremiumFailure(PremiumFailureCategory.NETWORK, requestId = "unset"),
    ),
    private val revokeResult: NativeAuthResult<Unit> = NativeAuthResult.Failure(
        PremiumFailure(PremiumFailureCategory.NETWORK, requestId = "unset"),
    ),
) : PremiumNativeAuthApi {
    var refreshCount = 0
    var revokeCount = 0

    override fun start(requestId: String): NativeAuthResult<DeviceAuthorizationStartV1Dto> = error("unused")
    override fun poll(deviceCode: DeviceCode, requestId: String): NativeAuthResult<TokenResponseV1Dto> = error("unused")
    override fun refresh(refreshToken: PremiumRefreshToken, requestId: String) = refreshResult.also { refreshCount += 1 }
    override fun revoke(refreshToken: PremiumRefreshToken, requestId: String) = revokeResult.also { revokeCount += 1 }
}
