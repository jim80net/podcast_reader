package net.jim80.podcastreader.core.premium

import android.content.Context
import java.util.Arrays
import kotlinx.serialization.Serializable
import net.jim80.podcastreader.core.security.CredentialStorageIdentity
import net.jim80.podcastreader.core.security.EncryptedRecordBackend
import net.jim80.podcastreader.core.security.KeystoreEncryptedRecordBackend
import net.jim80.podcastreader.core.security.PremiumAccountCredentialDomain
import net.jim80.podcastreader.core.security.secretBytes
import net.jim80.podcastreader.core.security.secretText

class PremiumCredentialStore internal constructor(
    private val backend: EncryptedRecordBackend<PremiumAccountCredentialDomain>,
) {
    fun save(credentials: PremiumAccountCredentials): Result<Unit> = runCatching {
        val plaintext = premiumJson.encodeToString(
            StoredPremiumCredentials(
                origin = credentials.origin.value,
                refreshToken = credentials.refreshToken.value,
            ),
        ).secretBytes()
        try {
            backend.write(plaintext)
        } finally {
            Arrays.fill(plaintext, 0)
        }
    }

    fun load(): Result<PremiumAccountCredentials?> = runCatching {
        val plaintext = backend.read() ?: return@runCatching null
        try {
            val stored = premiumJson.decodeFromString<StoredPremiumCredentials>(plaintext.secretText())
            PremiumAccountCredentials(
                origin = PremiumOrigin.fromTrustedConfiguration(stored.origin).getOrThrow(),
                refreshToken = PremiumRefreshToken.fromAuthorization(stored.refreshToken).getOrThrow(),
            )
        } finally {
            Arrays.fill(plaintext, 0)
        }
    }

    fun disconnectLocalRecord(): Result<Unit> = runCatching {
        backend.clear()
    }

    companion object {
        internal val storageIdentity = CredentialStorageIdentity.premiumAccount

        fun create(context: Context): PremiumCredentialStore = PremiumCredentialStore(
            KeystoreEncryptedRecordBackend(context.applicationContext, storageIdentity),
        )
    }
}

@Serializable
private data class StoredPremiumCredentials(
    val origin: String,
    val refreshToken: String,
)
