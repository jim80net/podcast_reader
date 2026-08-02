package net.jim80.podcastreader.core.security

import net.jim80.podcastreader.core.engine.EngineBearer
import net.jim80.podcastreader.core.engine.EngineCredentialStore
import net.jim80.podcastreader.core.engine.EnginePairingCredentials
import net.jim80.podcastreader.core.engine.TailnetOrigin
import net.jim80.podcastreader.core.premium.PremiumAccountCredentials
import net.jim80.podcastreader.core.premium.PremiumCredentialStore
import net.jim80.podcastreader.core.premium.PremiumOrigin
import net.jim80.podcastreader.core.premium.PremiumRefreshToken
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class CredentialIsolationTest {
    @Test
    fun trustDomainsUseDifferentRecordsAndKeystoreAliases() {
        assertFalse(EngineCredentialStore.storageIdentity == PremiumCredentialStore.storageIdentity)
        assertFalse(
            EngineCredentialStore.storageIdentity.keyAlias ==
                PremiumCredentialStore.storageIdentity.keyAlias,
        )
        assertFalse(
            EngineCredentialStore.storageIdentity.preferencesName ==
                PremiumCredentialStore.storageIdentity.preferencesName,
        )
    }

    @Test
    fun clearingPremiumCannotClearTheEngineRecord() {
        val engineBackend = FakeEncryptedRecordBackend(EngineCredentialStore.storageIdentity)
        val premiumBackend = FakeEncryptedRecordBackend(PremiumCredentialStore.storageIdentity)
        val engineStore = EngineCredentialStore(engineBackend)
        val premiumStore = PremiumCredentialStore(premiumBackend)

        engineStore.save(engineCredentials()).getOrThrow()
        val engineBytes = requireNotNull(engineBackend.bytes).copyOf()
        premiumStore.save(premiumCredentials()).getOrThrow()
        premiumStore.disconnectLocalRecord().getOrThrow()

        assertArrayEquals(engineBytes, engineBackend.bytes)
        assertEquals(0, engineBackend.clearCount)
        assertEquals(1, premiumBackend.clearCount)
        assertTrue(engineStore.load().getOrThrow() is EnginePairingCredentials)
        assertNull(premiumStore.load().getOrThrow())
    }

    @Test
    fun aDomainRejectsTheOtherDomainsBackendBeforeWriting() {
        val engineBackend = FakeEncryptedRecordBackend(EngineCredentialStore.storageIdentity)
        val premiumStoreOnEngineRecord = PremiumCredentialStore(engineBackend)

        assertTrue(premiumStoreOnEngineRecord.save(premiumCredentials()).isFailure)
        assertNull(engineBackend.bytes)
    }

    @Test
    fun eachRepositoryRejectsTheOtherDomainsRecordBytes() {
        val engineBackend = FakeEncryptedRecordBackend(EngineCredentialStore.storageIdentity)
        EngineCredentialStore(engineBackend).save(engineCredentials()).getOrThrow()
        val engineBytesUnderPremiumIdentity = FakeEncryptedRecordBackend(
            PremiumCredentialStore.storageIdentity,
        ).also { it.bytes = requireNotNull(engineBackend.bytes).copyOf() }
        assertTrue(PremiumCredentialStore(engineBytesUnderPremiumIdentity).load().isFailure)

        val premiumBackend = FakeEncryptedRecordBackend(PremiumCredentialStore.storageIdentity)
        PremiumCredentialStore(premiumBackend).save(premiumCredentials()).getOrThrow()
        val premiumBytesUnderEngineIdentity = FakeEncryptedRecordBackend(
            EngineCredentialStore.storageIdentity,
        ).also { it.bytes = requireNotNull(premiumBackend.bytes).copyOf() }
        assertTrue(EngineCredentialStore(premiumBytesUnderEngineIdentity).load().isFailure)
    }

    @Test
    fun aKeystoreFailureRemainsInsideItsOwnDomain() {
        val engineBackend = FakeEncryptedRecordBackend(EngineCredentialStore.storageIdentity)
        val premiumBackend = FakeEncryptedRecordBackend(
            PremiumCredentialStore.storageIdentity,
            failWrites = true,
        )
        val engineStore = EngineCredentialStore(engineBackend)

        engineStore.save(engineCredentials()).getOrThrow()
        assertTrue(PremiumCredentialStore(premiumBackend).save(premiumCredentials()).isFailure)
        assertTrue(engineStore.load().isSuccess)
        assertEquals(0, engineBackend.clearCount)
    }

    private fun engineCredentials() = EnginePairingCredentials(
        origin = TailnetOrigin.parse("https://desktop.example-tailnet.ts.net").getOrThrow(),
        bearer = EngineBearer.fromClaim("engine-secret-marker").getOrThrow(),
    )

    private fun premiumCredentials() = PremiumAccountCredentials(
        origin = PremiumOrigin.fromTrustedConfiguration("https://premium.example.ts.net:8443").getOrThrow(),
        refreshToken = PremiumRefreshToken.fromAuthorization("premium-refresh-marker-123456789").getOrThrow(),
    )
}

internal class FakeEncryptedRecordBackend(
    override val identity: CredentialStorageIdentity,
    private val failWrites: Boolean = false,
    private val failReads: Boolean = false,
    private val failClears: Boolean = false,
) : EncryptedRecordBackend {
    var bytes: ByteArray? = null
    var clearCount = 0

    override fun read(): ByteArray? {
        if (failReads) error("keystore read failure")
        return bytes?.copyOf()
    }

    override fun write(plaintext: ByteArray) {
        if (failWrites) error("keystore write failure")
        bytes = plaintext.copyOf()
    }

    override fun clear() {
        if (failClears) error("keystore clear failure")
        bytes = null
        clearCount += 1
    }
}
