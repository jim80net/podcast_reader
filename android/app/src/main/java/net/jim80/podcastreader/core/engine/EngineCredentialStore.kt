package net.jim80.podcastreader.core.engine

import android.content.Context
import java.util.Arrays
import kotlinx.serialization.Serializable
import net.jim80.podcastreader.core.security.CredentialStorageIdentity
import net.jim80.podcastreader.core.security.EncryptedRecordBackend
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
    private val backend: EncryptedRecordBackend,
) {
    fun save(credentials: EnginePairingCredentials): Result<Unit> = runCatching {
        require(backend.identity == storageIdentity) { "wrong credential domain" }
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
        require(backend.identity == storageIdentity) { "wrong credential domain" }
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
        require(backend.identity == storageIdentity) { "wrong credential domain" }
        backend.clear()
    }

    companion object {
        internal val storageIdentity = CredentialStorageIdentity(
            domain = "home_engine_pairing",
            keyAlias = "net.jim80.podcastreader.keystore.home_engine.v1",
            preferencesName = "home_engine_pairing_v1",
        )

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
