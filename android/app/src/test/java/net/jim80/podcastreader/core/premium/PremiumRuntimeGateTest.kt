package net.jim80.podcastreader.core.premium

import net.jim80.podcastreader.core.security.FakeEncryptedRecordBackend
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertSame
import org.junit.Assert.assertTrue
import org.junit.Test

class PremiumRuntimeGateTest {
    @Test
    fun localModeDoesNotInstantiatePremiumTransportOrAuthorizer() {
        var constructions = 0
        val runtime = PremiumRuntimeGate.create(null) {
            constructions += 1
            error("local mode constructed premium runtime")
        }

        assertNull(runtime)
        assertEquals(0, constructions)
    }

    @Test
    fun authorizerPersistsOnlyRefreshStateAndKeepsAccessInMemory() {
        val backend = FakeEncryptedRecordBackend(PremiumCredentialStore.storageIdentity)
        val store = PremiumCredentialStore(backend)
        val authorizer = PremiumAccountAuthorizer(store)
        val credentials = PremiumAccountCredentials(
            PremiumOrigin.fromTrustedConfiguration("https://premium.example.ts.net:8443").getOrThrow(),
            PremiumRefreshToken.fromAuthorization("premium-refresh-marker-123456789").getOrThrow(),
        )
        val access = PremiumAccessToken.fromAuthorization("premium-access-marker-1234567890").getOrThrow()

        authorizer.installAuthorizedSession(credentials, access).getOrThrow()

        assertSame(access, authorizer.currentAccessToken())
        val restored = requireNotNull(PremiumAccountAuthorizer(store).restoreAccountRecord().getOrThrow())
        assertEquals(credentials.origin.value, restored.origin.value)
        assertEquals(credentials.refreshToken.value, restored.refreshToken.value)
        assertNull(PremiumAccountAuthorizer(store).currentAccessToken())
        assertFalseContains(backend.bytes, "premium-access-marker")
    }

    @Test
    fun localDisconnectDropsAccessAndOnlyThePremiumRecord() {
        val backend = FakeEncryptedRecordBackend(PremiumCredentialStore.storageIdentity)
        val authorizer = PremiumAccountAuthorizer(PremiumCredentialStore(backend))
        val credentials = PremiumAccountCredentials(
            PremiumOrigin.fromTrustedConfiguration("https://premium.example.ts.net:8443").getOrThrow(),
            PremiumRefreshToken.fromAuthorization("premium-refresh-marker-123456789").getOrThrow(),
        )
        val access = PremiumAccessToken.fromAuthorization("premium-access-marker-1234567890").getOrThrow()
        authorizer.installAuthorizedSession(credentials, access).getOrThrow()

        authorizer.disconnectLocalRecord().getOrThrow()

        assertNull(authorizer.currentAccessToken())
        assertNull(backend.bytes)
        assertEquals(1, backend.clearCount)
    }

    private fun assertFalseContains(bytes: ByteArray?, marker: String) {
        assertTrue(bytes != null)
        assertTrue(!requireNotNull(bytes).decodeToString().contains(marker))
    }
}
