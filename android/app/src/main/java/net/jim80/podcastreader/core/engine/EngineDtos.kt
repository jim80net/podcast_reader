package net.jim80.podcastreader.core.engine

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json

internal val engineJson = Json {
    ignoreUnknownKeys = true
    isLenient = false
    allowSpecialFloatingPointValues = false
    explicitNulls = true
}

@Serializable
data class HealthDto(
    val version: String,
    @SerialName("token_fingerprint") val tokenFingerprint: String,
) {
    override fun toString(): String = "HealthDto(version=$version, tokenFingerprint=redacted)"
}

@Serializable
class PairClaimDto(val token: String) {
    override fun toString(): String = "PairClaimDto(token=redacted)"
}

@Serializable
data class LibraryEntryDto(
    @SerialName("source_id") val sourceId: String,
    val source: String,
    val title: String,
    @SerialName("html_path") val htmlPath: String,
    @SerialName("created_at") val createdAt: Double,
) {
    override fun toString(): String = "LibraryEntryDto(redacted)"

    fun toSummary(): Result<LibrarySummary> = runCatching {
        require(Regex("[0-9a-f]{64}").matches(sourceId)) { "invalid library entry" }
        require(title.isNotBlank() && createdAt.isFinite()) { "invalid library entry" }
        LibrarySummary(sourceId = sourceId, title = title, createdAt = createdAt)
    }
}

data class LibrarySummary(
    val sourceId: String,
    val title: String,
    val createdAt: Double,
) {
    override fun toString(): String = "LibrarySummary(redacted)"
}
