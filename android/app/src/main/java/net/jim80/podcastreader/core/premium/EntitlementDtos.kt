package net.jim80.podcastreader.core.premium

import java.time.Instant
import kotlinx.serialization.KSerializer
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.SerializationException
import kotlinx.serialization.descriptors.PrimitiveKind
import kotlinx.serialization.descriptors.PrimitiveSerialDescriptor
import kotlinx.serialization.descriptors.SerialDescriptor
import kotlinx.serialization.encoding.Decoder
import kotlinx.serialization.encoding.Encoder
import kotlinx.serialization.json.JsonDecoder
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.longOrNull

private const val MAX_SAFE_REVISION = 9_007_199_254_740_991L
private val CANONICAL_UTC_SECONDS = Regex("^\\d{4}-\\d{2}-\\d{2}T\\d{2}:\\d{2}:\\d{2}Z$")

internal val premiumJson = Json {
    ignoreUnknownKeys = false
    isLenient = false
    allowSpecialFloatingPointValues = false
    explicitNulls = true
}

private object StrictLongSerializer : KSerializer<Long> {
    override val descriptor: SerialDescriptor = PrimitiveSerialDescriptor("StrictLong", PrimitiveKind.LONG)

    override fun deserialize(decoder: Decoder): Long {
        val jsonDecoder = decoder as? JsonDecoder
            ?: throw SerializationException("strict integer requires JSON input")
        val primitive = jsonDecoder.decodeJsonElement() as? JsonPrimitive
            ?: throw SerializationException("strict integer must be a JSON number")
        if (primitive.isString) {
            throw SerializationException("strict integer must not be quoted")
        }
        return primitive.longOrNull
            ?: throw SerializationException("strict integer is outside the supported range")
    }

    override fun serialize(encoder: Encoder, value: Long) = encoder.encodeLong(value)
}

@Serializable
data class EntitlementV1Dto(
    @SerialName("schema_version") val schemaVersion: Int,
    val subject: String,
    val tier: EntitlementTierDto,
    val entitlement: EntitlementSourceDto,
    val capabilities: EntitlementCapabilitiesDto,
    @Serializable(with = StrictLongSerializer::class)
    @SerialName("flags_revision") val flagsRevision: Long,
    @SerialName("evaluated_at") val evaluatedAt: String,
    @SerialName("refresh_after") val refreshAfter: String,
) {
    override fun toString(): String = "EntitlementV1Dto(redacted)"

    internal fun validated(expectedSubject: String): Result<EntitlementProjection> =
        EntitlementProjection.validated(this, expectedSubject)
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
    @Serializable(with = StrictLongSerializer::class)
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

internal class EntitlementProjection private constructor(
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

    companion object {
        fun validated(dto: EntitlementV1Dto, expectedSubject: String): Result<EntitlementProjection> = runCatching {
            require(dto.schemaVersion == 1) { "unsupported entitlement schema" }
            require(dto.subject == expectedSubject && dto.subject.isNotBlank()) { "entitlement subject mismatch" }
            require(dto.entitlement.revision in 0..MAX_SAFE_REVISION && dto.flagsRevision in 0..MAX_SAFE_REVISION) {
                "invalid entitlement revision"
            }
            val evaluated = parseCanonicalUtc(dto.evaluatedAt)
            val refresh = parseCanonicalUtc(dto.refreshAfter)
            require(refresh.isAfter(evaluated)) { "invalid entitlement refresh window" }

            when (dto.tier) {
                EntitlementTierDto.FREE -> {
                    require(
                        dto.entitlement.source == EntitlementSourceKindDto.NONE ||
                            dto.entitlement.source == EntitlementSourceKindDto.ADMIN,
                    )
                    require(dto.capabilities.adPolicy == AdPolicyDto.NONE || dto.capabilities.adPolicy == AdPolicyDto.HOUSE)
                    require(!dto.capabilities.podcastSubscriptions)
                    require(!dto.capabilities.transcriptEmail)
                    require(!dto.capabilities.mobileAdFree)
                    require(!dto.capabilities.topicCorpus)
                }

                EntitlementTierDto.PREMIUM -> {
                    require(
                        dto.entitlement.source == EntitlementSourceKindDto.TEST_PURCHASE ||
                            dto.entitlement.source == EntitlementSourceKindDto.ADMIN,
                    )
                    require(dto.capabilities.adPolicy == AdPolicyDto.NONE)
                }
            }

            EntitlementProjection(
                subject = dto.subject,
                tier = dto.tier,
                source = dto.entitlement.source,
                entitlementRevision = dto.entitlement.revision,
                capabilities = dto.capabilities,
                flagsRevision = dto.flagsRevision,
                evaluatedAt = evaluated,
                refreshAfter = refresh,
            )
        }
    }
}

private fun parseCanonicalUtc(value: String): Instant {
    require(CANONICAL_UTC_SECONDS.matches(value)) { "entitlement timestamp must be canonical UTC seconds" }
    return Instant.parse(value).also {
        require(it.toString() == value) { "entitlement timestamp is not canonical" }
    }
}
