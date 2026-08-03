package net.jim80.podcastreader.core.ads

import java.net.URI
import java.time.Instant
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json

internal val houseAdJson = Json {
    ignoreUnknownKeys = true
    isLenient = false
    allowSpecialFloatingPointValues = false
    explicitNulls = true
}

@Serializable
data class HouseInventoryV1Dto(
    @SerialName("schema_version") val schemaVersion: Int,
    val slot: String,
    @SerialName("inventory_revision") val inventoryRevision: Long,
    @SerialName("expires_at") val expiresAt: String,
    val items: List<HouseInventoryItemV1Dto>,
) {
    override fun toString(): String = "HouseInventoryV1Dto(redacted)"

    internal fun validated(
        placement: HouseAdPlacement,
        now: Instant,
        entitlementValidUntil: Instant,
    ): Result<HouseInventory> = runCatching {
        require(schemaVersion == 1 && slot == placement.backendSlot) { "inventory slot mismatch" }
        require(inventoryRevision >= 0 && items.size in 1..10) { "invalid inventory bounds" }
        val expiry = parseCanonicalUtc(expiresAt)
        require(
            expiry.isAfter(now) &&
                !expiry.isAfter(now.plusSeconds(MAX_INVENTORY_LIFETIME_SECONDS)) &&
                !expiry.isAfter(entitlementValidUntil),
        ) { "invalid inventory expiry" }
        val creatives = items.map { it.validated() }
        require(creatives.map { it.id }.distinct().size == creatives.size) { "duplicate inventory item" }
        HouseInventory(placement, inventoryRevision, expiry, creatives)
    }
}

@Serializable
data class HouseInventoryItemV1Dto(
    val id: String,
    val revision: Long,
    val kind: String,
    val title: String,
    val body: String,
    @SerialName("cta_url") val ctaUrl: String,
) {
    override fun toString(): String = "HouseInventoryItemV1Dto(redacted)"

    internal fun validated(): HouseAdCreative {
        require(ID.matches(id) && id.length in 4..40 && revision >= 1) { "invalid inventory item identity" }
        require(kind == "text" && title.length in 1..120 && body.length in 1..500) {
            "invalid inventory text"
        }
        return HouseAdCreative(id, revision, title, body, HouseAdCta.fromContract(ctaUrl).getOrThrow())
    }

    private companion object {
        val ID = Regex("^ad_[A-Za-z0-9_-]+$")
    }
}

@Serializable
internal data class NoContentFixtureV1(
    @SerialName("schema_version") val schemaVersion: Int,
    val status: Int,
    val body: Nothing?,
    val meaning: String,
)

enum class HouseAdPlacement(internal val backendSlot: String) {
    LIBRARY("library"),
    JOBS("mobile_home"),
}

data class HouseInventory(
    val placement: HouseAdPlacement,
    val inventoryRevision: Long,
    val expiresAt: Instant,
    val items: List<HouseAdCreative>,
) {
    override fun toString(): String = "HouseInventory(redacted)"
}

data class HouseAdCreative(
    val id: String,
    val revision: Long,
    val title: String,
    val body: String,
    val cta: HouseAdCta,
) {
    override fun toString(): String = "HouseAdCreative(redacted)"
}

class HouseAdCta private constructor(val value: String) {
    override fun toString(): String = "HouseAdCta(redacted)"

    companion object {
        fun fromContract(value: String): Result<HouseAdCta> = runCatching {
            require(value.length in 1..2048 && value == value.trim() && value.none(Char::isWhitespace)) {
                "invalid CTA"
            }
            val uri = URI(value)
            require(
                uri.scheme == "https" && uri.host != null && uri.rawUserInfo == null &&
                    uri.rawQuery == null && uri.rawFragment == null &&
                    (uri.port == -1 || uri.port in 1..65535),
            ) { "unsafe CTA" }
            HouseAdCta(value)
        }
    }
}

private fun parseCanonicalUtc(value: String): Instant {
    require(value.endsWith('Z')) { "inventory expiry must be UTC" }
    return Instant.parse(value).also { require(it.toString() == value) { "inventory expiry is not canonical" } }
}

private const val MAX_INVENTORY_LIFETIME_SECONDS = 300L
