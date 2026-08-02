package net.jim80.podcastreader.core.security

import android.annotation.SuppressLint
import android.content.Context
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import java.nio.charset.StandardCharsets
import java.security.KeyStore
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

internal data class CredentialStorageIdentity(
    val domain: String,
    val keyAlias: String,
    val preferencesName: String,
)

internal interface EncryptedRecordBackend {
    val identity: CredentialStorageIdentity

    fun read(): ByteArray?

    fun write(plaintext: ByteArray)

    fun clear()
}

internal class KeystoreEncryptedRecordBackend(
    context: Context,
    override val identity: CredentialStorageIdentity,
) : EncryptedRecordBackend {
    private val preferences = context.getSharedPreferences(identity.preferencesName, Context.MODE_PRIVATE)

    override fun read(): ByteArray? {
        val ciphertextText = preferences.getString(CIPHERTEXT_KEY, null)
        val nonceText = preferences.getString(NONCE_KEY, null)
        if (ciphertextText == null && nonceText == null) return null
        require(ciphertextText != null && nonceText != null) { "encrypted record is incomplete" }

        val key = loadExistingKey() ?: error("encrypted record key is unavailable")
        val cipher = Cipher.getInstance(TRANSFORMATION)
        val nonce = Base64.decode(nonceText, Base64.NO_WRAP)
        require(nonce.size == NONCE_BYTES) { "encrypted record nonce is invalid" }
        cipher.init(Cipher.DECRYPT_MODE, key, GCMParameterSpec(TAG_BITS, nonce))
        return cipher.doFinal(Base64.decode(ciphertextText, Base64.NO_WRAP))
    }

    @SuppressLint("UseKtx") // commit() must expose persistence failure for credential writes.
    override fun write(plaintext: ByteArray) {
        require(plaintext.isNotEmpty() && plaintext.size <= MAX_RECORD_BYTES) {
            "encrypted record size is invalid"
        }
        val cipher = Cipher.getInstance(TRANSFORMATION)
        cipher.init(Cipher.ENCRYPT_MODE, loadOrCreateKey())
        val ciphertext = cipher.doFinal(plaintext)
        check(cipher.iv.size == NONCE_BYTES) { "encrypted record nonce is invalid" }
        check(
            preferences.edit()
                .putString(CIPHERTEXT_KEY, Base64.encodeToString(ciphertext, Base64.NO_WRAP))
                .putString(NONCE_KEY, Base64.encodeToString(cipher.iv, Base64.NO_WRAP))
                .commit(),
        ) { "encrypted record could not be committed" }
    }

    @SuppressLint("UseKtx") // commit() must expose persistence failure for credential deletion.
    override fun clear() {
        check(preferences.edit().clear().commit()) { "encrypted record could not be cleared" }
        val keyStore = keyStore()
        if (keyStore.containsAlias(identity.keyAlias)) keyStore.deleteEntry(identity.keyAlias)
    }

    private fun loadExistingKey(): SecretKey? = keyStore().getKey(identity.keyAlias, null) as? SecretKey

    private fun loadOrCreateKey(): SecretKey = loadExistingKey() ?: KeyGenerator
        .getInstance(KeyProperties.KEY_ALGORITHM_AES, ANDROID_KEYSTORE)
        .apply {
            init(
                KeyGenParameterSpec.Builder(
                    identity.keyAlias,
                    KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT,
                )
                    .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                    .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                    .setRandomizedEncryptionRequired(true)
                    .setKeySize(256)
                    .build(),
            )
        }
        .generateKey()

    private fun keyStore(): KeyStore = KeyStore.getInstance(ANDROID_KEYSTORE).apply { load(null) }

    private companion object {
        const val ANDROID_KEYSTORE = "AndroidKeyStore"
        const val TRANSFORMATION = "AES/GCM/NoPadding"
        const val CIPHERTEXT_KEY = "ciphertext"
        const val NONCE_KEY = "nonce"
        const val NONCE_BYTES = 12
        const val TAG_BITS = 128
        const val MAX_RECORD_BYTES = 16 * 1024
    }
}

internal fun String.secretBytes(): ByteArray = toByteArray(StandardCharsets.UTF_8)

internal fun ByteArray.secretText(): String = String(this, StandardCharsets.UTF_8)
