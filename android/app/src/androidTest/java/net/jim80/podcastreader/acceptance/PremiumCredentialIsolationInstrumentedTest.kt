package net.jim80.podcastreader.acceptance

import android.content.Context
import androidx.test.core.app.ApplicationProvider
import androidx.test.ext.junit.runners.AndroidJUnit4
import java.io.File
import java.nio.charset.StandardCharsets
import java.security.KeyStore
import net.jim80.podcastreader.core.engine.EngineBearer
import net.jim80.podcastreader.core.engine.EngineCredentialStore
import net.jim80.podcastreader.core.engine.EnginePairingCredentials
import net.jim80.podcastreader.core.engine.TailnetOrigin
import net.jim80.podcastreader.core.premium.PremiumAccountCredentials
import net.jim80.podcastreader.core.premium.PremiumCredentialStore
import net.jim80.podcastreader.core.premium.PremiumOrigin
import net.jim80.podcastreader.core.premium.PremiumRefreshToken
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith

@RunWith(AndroidJUnit4::class)
class PremiumCredentialIsolationInstrumentedTest {
    private val context = ApplicationProvider.getApplicationContext<Context>()
    private val engineStore = EngineCredentialStore.create(context)
    private val premiumStore = PremiumCredentialStore.create(context)

    @Test
    fun keystoreRecordsAreCiphertextAndCannotCrossClearOrCrossInvalidate() {
        engineStore.forget().getOrThrow()
        premiumStore.disconnectLocalRecord().getOrThrow()

        val engine = EnginePairingCredentials(
            TailnetOrigin.parse("https://reader-device.example-tailnet.ts.net").getOrThrow(),
            EngineBearer.fromClaim(AcceptanceSecrets.ENGINE_BEARER).getOrThrow(),
        )
        val premium = PremiumAccountCredentials(
            PremiumOrigin.fromTrustedConfiguration("https://premium.example.com").getOrThrow(),
            PremiumRefreshToken.fromAuthorization(AcceptanceSecrets.PREMIUM_REFRESH).getOrThrow(),
        )
        engineStore.save(engine).getOrThrow()
        premiumStore.save(premium).getOrThrow()

        val engineRecordBefore = preferencesBytes(EngineCredentialStore.storageIdentity.preferencesName)
        val premiumRecordBefore = preferencesBytes(PremiumCredentialStore.storageIdentity.preferencesName)
        assertNoPlaintextMarkers(engineRecordBefore, premiumRecordBefore)
        assertFalse(engineRecordBefore.contentEquals(premiumRecordBefore))

        val keyStore = androidKeyStore()
        assertTrue(keyStore.containsAlias(EngineCredentialStore.storageIdentity.keyAlias))
        assertTrue(keyStore.containsAlias(PremiumCredentialStore.storageIdentity.keyAlias))

        engineStore.forget().getOrThrow()
        assertFalse(androidKeyStore().containsAlias(EngineCredentialStore.storageIdentity.keyAlias))
        assertTrue(androidKeyStore().containsAlias(PremiumCredentialStore.storageIdentity.keyAlias))
        assertTrue(premiumStore.load().getOrThrow() != null)
        assertArrayEquals(
            premiumRecordBefore,
            preferencesBytes(PremiumCredentialStore.storageIdentity.preferencesName),
        )

        engineStore.save(engine).getOrThrow()
        premiumStore.disconnectLocalRecord().getOrThrow()
        assertTrue(engineStore.load().getOrThrow() != null)
        assertFalse(androidKeyStore().containsAlias(PremiumCredentialStore.storageIdentity.keyAlias))
        assertTrue(androidKeyStore().containsAlias(EngineCredentialStore.storageIdentity.keyAlias))

        premiumStore.save(premium).getOrThrow()
        androidKeyStore().deleteEntry(PremiumCredentialStore.storageIdentity.keyAlias)
        assertTrue(premiumStore.load().isFailure)
        assertTrue(engineStore.load().getOrThrow() != null)

        premiumStore.disconnectLocalRecord().getOrThrow()
        premiumStore.save(premium).getOrThrow()
        assertNoPlaintextMarkers(
            preferencesBytes(EngineCredentialStore.storageIdentity.preferencesName),
            preferencesBytes(PremiumCredentialStore.storageIdentity.preferencesName),
        )
        // Deliberately leave both encrypted records for the post-test run-as K4 sweep.
    }

    private fun preferencesBytes(name: String): ByteArray = File(
        context.applicationInfo.dataDir,
        "shared_prefs/$name.xml",
    ).readBytes()

    private fun assertNoPlaintextMarkers(vararg records: ByteArray) {
        records.forEach { bytes ->
            val text = String(bytes, StandardCharsets.UTF_8)
            AcceptanceSecrets.fullAndPrefixMarkers.forEach { marker ->
                assertFalse("plaintext marker reached credential storage", text.contains(marker))
            }
        }
    }

    private fun androidKeyStore(): KeyStore = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
}
