package net.jim80.podcastreader.core.engine

import android.content.Context
import java.util.Arrays
import kotlinx.serialization.Serializable
import net.jim80.podcastreader.core.security.CredentialStorageIdentity
import net.jim80.podcastreader.core.security.EncryptedRecordBackend
import net.jim80.podcastreader.core.security.EnginePairingCredentialDomain
import net.jim80.podcastreader.core.security.KeystoreEncryptedRecordBackend
import net.jim80.podcastreader.core.security.secretBytes
import net.jim80.podcastreader.core.security.secretText

data class EnginePairingCredentials(
    val origin: TailnetOrigin,
    val bearer: EngineBearer,
) {
    override fun toString(): String = "EnginePairingCredentials(redacted)"
}

class EngineCredentialStore internal constructor(
    private val backend: EncryptedRecordBackend<EnginePairingCredentialDomain>,
) {
    fun save(credentials: EnginePairingCredentials): Result<Unit> = runCatching {
        val plaintext = engineJson.encodeToString(
            StoredEngineCredentials(
                origin = credentials.origin.value,
                bearer = credentials.bearer.value,
            ),
        ).secretBytes()
        try {
            backend.write(plaintext)
        } finally {
            Arrays.fill(plaintext, 0)
        }
    }

    fun load(): Result<EnginePairingCredentials?> = runCatching {
        val plaintext = backend.read() ?: return@runCatching null
        try {
            val stored = engineJson.decodeFromString<StoredEngineCredentials>(plaintext.secretText())
            EnginePairingCredentials(
                origin = TailnetOrigin.parse(stored.origin).getOrThrow(),
                bearer = EngineBearer.fromClaim(stored.bearer).getOrThrow(),
            )
        } finally {
            Arrays.fill(plaintext, 0)
        }
    }

    fun forget(): Result<Unit> = runCatching {
        backend.clear()
    }

    companion object {
        internal val storageIdentity = CredentialStorageIdentity.enginePairing

        fun create(context: Context): EngineCredentialStore = EngineCredentialStore(
            KeystoreEncryptedRecordBackend(context.applicationContext, storageIdentity),
        )
    }
}

@Serializable
private data class StoredEngineCredentials(
    val origin: String,
    val bearer: String,
)
