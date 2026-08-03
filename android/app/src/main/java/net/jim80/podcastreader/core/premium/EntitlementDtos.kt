package net.jim80.podcastreader.core.premium

import java.time.Instant
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json

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
        require(entitlement.revision >= 0 && flagsRevision >= 0) { "invalid entitlement revision" }
        val evaluated = parseCanonicalUtc(evaluatedAt)
        val refresh = parseCanonicalUtc(refreshAfter)
        require(refresh.isAfter(evaluated)) { "invalid entitlement refresh window" }

        when (tier) {
            EntitlementTierDto.FREE -> {
                require(entitlement.source == EntitlementSourceKindDto.NONE)
                require(capabilities.adPolicy == AdPolicyDto.NONE || capabilities.adPolicy == AdPolicyDto.HOUSE)
                require(!capabilities.podcastSubscriptions)
                require(!capabilities.transcriptEmail)
                require(!capabilities.mobileAdFree)
                require(!capabilities.topicCorpus)
            }

            EntitlementTierDto.PREMIUM -> {
                require(
                    entitlement.source == EntitlementSourceKindDto.TEST_PURCHASE,
                )
                require(capabilities.adPolicy == AdPolicyDto.NONE)
                require(capabilities.mobileAdFree)
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
    require(value.endsWith('Z')) { "entitlement timestamp must be UTC" }
    return Instant.parse(value).also {
        require(it.toString() == value) { "entitlement timestamp is not canonical" }
    }
}
