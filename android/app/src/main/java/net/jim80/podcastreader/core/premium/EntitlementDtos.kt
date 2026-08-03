package net.jim80.podcastreader.core.premium

import java.time.Instant
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json

private const val MAX_SAFE_REVISION = 9_007_199_254_740_991L
private val CANONICAL_UTC_SECONDS = Regex("^\\d{4}-\\d{2}-\\d{2}T\\d{2}:\\d{2}:\\d{2}Z$")

internal val premiumJson = Json {
    ignoreUnknownKeys = false
    isLenient = false
    allowSpecialFloatingPointValues = false
    explicitNulls = true
}

@Serializable
data class EntitlementV1Dto(
    @SerialName("schema_version") val schemaVersion: Int,
    val subject: String,
    val tier: EntitlementTierDto,
    val entitlement: EntitlementSourceDto,
    val capabilities: EntitlementCapabilitiesDto,
    @SerialName("flags_revision") val flagsRevision: Long,
    @SerialName("evaluated_at") val evaluatedAt: String,
    @SerialName("refresh_after") val refreshAfter: String,
) {
    override fun toString(): String = "EntitlementV1Dto(redacted)"

    internal fun validated(expectedSubject: String): Result<EntitlementProjection> = runCatching {
        require(schemaVersion == 1) { "unsupported entitlement schema" }
        require(subject == expectedSubject && subject.isNotBlank()) { "entitlement subject mismatch" }
        require(entitlement.revision in 0..MAX_SAFE_REVISION && flagsRevision in 0..MAX_SAFE_REVISION) {
            "invalid entitlement revision"
        }
        val evaluated = parseCanonicalUtc(evaluatedAt)
        val refresh = parseCanonicalUtc(refreshAfter)
        require(refresh.isAfter(evaluated)) { "invalid entitlement refresh window" }

        when (tier) {
            EntitlementTierDto.FREE -> {
                require(
                    entitlement.source == EntitlementSourceKindDto.NONE ||
                        entitlement.source == EntitlementSourceKindDto.ADMIN,
                )
                require(capabilities.adPolicy == AdPolicyDto.NONE || capabilities.adPolicy == AdPolicyDto.HOUSE)
                require(!capabilities.podcastSubscriptions)
                require(!capabilities.transcriptEmail)
                require(!capabilities.mobileAdFree)
                require(!capabilities.topicCorpus)
            }

            EntitlementTierDto.PREMIUM -> {
                require(
                    entitlement.source == EntitlementSourceKindDto.TEST_PURCHASE ||
                        entitlement.source == EntitlementSourceKindDto.ADMIN,
                )
                require(capabilities.adPolicy == AdPolicyDto.NONE)
            }
        }

        EntitlementProjection(
            subject = subject,
            tier = tier,
            source = entitlement.source,
            entitlementRevision = entitlement.revision,
            capabilities = capabilities,
            flagsRevision = flagsRevision,
            evaluatedAt = evaluated,
            refreshAfter = refresh,
        )
    }
}

@Serializable
enum class EntitlementTierDto {
    @SerialName("free")
    FREE,

    @SerialName("premium")
    PREMIUM,
}

@Serializable
data class EntitlementSourceDto(
    val source: EntitlementSourceKindDto,
    val revision: Long,
)

@Serializable
enum class EntitlementSourceKindDto {
    @SerialName("none")
    NONE,

    @SerialName("test_purchase")
    TEST_PURCHASE,

    @SerialName("admin")
    ADMIN,

}

@Serializable
data class EntitlementCapabilitiesDto(
    @SerialName("ad_policy") val adPolicy: AdPolicyDto,
    @SerialName("podcast_subscriptions") val podcastSubscriptions: Boolean,
    @SerialName("transcript_email") val transcriptEmail: Boolean,
    @SerialName("mobile_ad_free") val mobileAdFree: Boolean,
    @SerialName("topic_corpus") val topicCorpus: Boolean,
)

@Serializable
enum class AdPolicyDto {
    @SerialName("none")
    NONE,

    @SerialName("house")
    HOUSE,

}

data class EntitlementProjection(
    val subject: String,
    val tier: EntitlementTierDto,
    val source: EntitlementSourceKindDto,
    val entitlementRevision: Long,
    val capabilities: EntitlementCapabilitiesDto,
    val flagsRevision: Long,
    val evaluatedAt: Instant,
    val refreshAfter: Instant,
) {
    override fun toString(): String = "EntitlementProjection(redacted)"
}

private fun parseCanonicalUtc(value: String): Instant {
    require(CANONICAL_UTC_SECONDS.matches(value)) { "entitlement timestamp must be canonical UTC seconds" }
    return Instant.parse(value).also {
        require(it.toString() == value) { "entitlement timestamp is not canonical" }
    }
}
