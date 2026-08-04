package net.jim80.podcastreader.core.premium

import java.time.Instant
import kotlinx.coroutines.test.runTest
import net.jim80.podcastreader.core.security.FakeEncryptedRecordBackend
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class PremiumConnectedSessionTest {
    private val now = Instant.parse("2026-08-02T00:01:00Z")
    private val origin = PremiumOrigin.fromTrustedConfiguration("https://premium.example.test").getOrThrow()

    @Test
    fun currentUserSubjectBindsTheCommittedEntitlementBeforeTruthProjection() = runTest {
        val calls = mutableListOf<String>()
        val session = session(
            currentSubject = "usr_free_fixture",
            calls = calls,
        )

        val result = session.restore(now, "restore-1")

        assertEquals(listOf("refresh", "current-user", "entitlements"), calls)
        assertTrue(result is PremiumRestoreResult.Online)
        assertEquals("online-free", requireOnline(result).kind())
    }

    @Test
    fun entitlementCannotSupplyItsOwnExpectedSubject() = runTest {
        val result = session(currentSubject = "different-subject").restore(now, "restore-2")

        assertTrue(result is PremiumRestoreResult.Online)
        assertEquals("unavailable", requireOnline(result).kind())
    }

    @Test
    fun newlyAuthorizedSessionValidatesTruthWithoutRefreshingAgain() = runTest {
        val calls = mutableListOf<String>()
        val result = session(
            currentSubject = "usr_free_fixture",
            calls = calls,
            authorized = true,
        ).validateAuthorized(now, "authorized-1")

        assertEquals(listOf("current-user", "entitlements"), calls)
        assertEquals("online-free", requireOnline(result).kind())
    }

    private fun session(
        currentSubject: String,
        calls: MutableList<String> = mutableListOf(),
        authorized: Boolean = false,
    ): ProductionPremiumConnectedSession {
        val store = PremiumCredentialStore(FakeEncryptedRecordBackend(PremiumCredentialStore.storageIdentity))
        store.save(
            PremiumAccountCredentials(
                origin,
                PremiumRefreshToken.fromAuthorization("old_refresh_token_abcdefghijklmnopqrstuvwxyz").getOrThrow(),
            ),
        ).getOrThrow()
        val authorizer = PremiumAccountAuthorizer(store)
        if (authorized) {
            authorizer.completeDeviceAuthorization(
                origin,
                AuthorizedPremiumTokens(
                    PremiumAccessToken.fromAuthorization("new_access_token_abcdefghijklmnopqrstuvwxyz").getOrThrow(),
                    PremiumRefreshToken.fromAuthorization("new_refresh_token_abcdefghijklmnopqrstuvwxyz").getOrThrow(),
                ),
            ).getOrThrow()
        }
        return ProductionPremiumConnectedSession(
            authorizer = authorizer,
            nativeAuth = SessionNativeAuthApi(calls),
            currentUser = SessionCurrentUserApi(currentSubject, calls),
            entitlements = SessionEntitlementApi(entitlementFixture(), calls),
        )
    }

    private fun entitlementFixture(): EntitlementV1Dto = premiumJson.decodeFromString(
        requireNotNull(javaClass.classLoader?.getResource("entitlements-v1-free.json")) {
            "missing backend-owned entitlement fixture"
        }.readText(),
    )

    private fun requireOnline(result: PremiumRestoreResult): ProductState =
        (result as PremiumRestoreResult.Online).productState

    private fun ProductState.kind(): String = fold(
        onLocal = { "local" },
        onOnlineFree = { "online-free" },
        onOnlinePremium = { "online-premium" },
        onOnlineUnavailable = { "unavailable" },
    )
}

private class SessionNativeAuthApi(
    private val calls: MutableList<String>,
) : PremiumNativeAuthApi {
    override fun start(requestId: String): NativeAuthResult<DeviceAuthorizationStartV1Dto> = error("unused")
    override fun poll(deviceCode: DeviceCode, requestId: String): NativeAuthResult<TokenResponseV1Dto> = error("unused")

    override fun refresh(refreshToken: PremiumRefreshToken, requestId: String): NativeAuthResult<TokenResponseV1Dto> {
        calls += "refresh"
        return NativeAuthResult.Success(
            TokenResponseV1Dto(
                accessToken = "new_access_token_abcdefghijklmnopqrstuvwxyz",
                tokenType = "Bearer",
                expiresIn = 900,
                refreshToken = "new_refresh_token_abcdefghijklmnopqrstuvwxyz",
            ),
        )
    }

    override fun revoke(refreshToken: PremiumRefreshToken, requestId: String): NativeAuthResult<Unit> =
        NativeAuthResult.Success(Unit)
}

private class SessionCurrentUserApi(
    private val subject: String,
    private val calls: MutableList<String>,
) : PremiumCurrentUserApi {
    override fun fetch(token: PremiumAccessToken, requestId: String): CurrentUserFetchResult {
        calls += "current-user"
        return CurrentUserFetchResult.Success(subject)
    }
}

private class SessionEntitlementApi(
    private val entitlement: EntitlementV1Dto,
    private val calls: MutableList<String>,
) : PremiumEntitlementApi {
    override fun fetch(token: PremiumAccessToken, requestId: String): EntitlementFetchResult {
        calls += "entitlements"
        return EntitlementFetchResult.Success(entitlement)
    }
}
