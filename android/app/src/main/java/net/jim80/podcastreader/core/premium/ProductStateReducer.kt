package net.jim80.podcastreader.core.premium

import java.time.Instant

sealed class ProductState private constructor() {
    abstract fun <T> fold(
        onLocal: () -> T,
        onOnlineFree: (OnlineFreeTruth) -> T,
        onOnlinePremium: (OnlinePremiumTruth) -> T,
        onOnlineUnavailable: (OnlineUnavailableReason) -> T,
    ): T

    private data object LocalState : ProductState() {
        override fun <T> fold(
            onLocal: () -> T,
            onOnlineFree: (OnlineFreeTruth) -> T,
            onOnlinePremium: (OnlinePremiumTruth) -> T,
            onOnlineUnavailable: (OnlineUnavailableReason) -> T,
        ): T = onLocal()
    }

    private class OnlineFreeState(
        private val truth: OnlineFreeTruth,
    ) : ProductState() {
        override fun <T> fold(
            onLocal: () -> T,
            onOnlineFree: (OnlineFreeTruth) -> T,
            onOnlinePremium: (OnlinePremiumTruth) -> T,
            onOnlineUnavailable: (OnlineUnavailableReason) -> T,
        ): T = onOnlineFree(truth)

        override fun toString(): String = "ProductState.OnlineFree(redacted)"
    }

    private class OnlinePremiumState(
        private val truth: OnlinePremiumTruth,
    ) : ProductState() {
        override fun <T> fold(
            onLocal: () -> T,
            onOnlineFree: (OnlineFreeTruth) -> T,
            onOnlinePremium: (OnlinePremiumTruth) -> T,
            onOnlineUnavailable: (OnlineUnavailableReason) -> T,
        ): T = onOnlinePremium(truth)

        override fun toString(): String = "ProductState.OnlinePremium(redacted)"
    }

    private data class OnlineUnavailableState(
        private val reason: OnlineUnavailableReason,
    ) : ProductState() {
        override fun <T> fold(
            onLocal: () -> T,
            onOnlineFree: (OnlineFreeTruth) -> T,
            onOnlinePremium: (OnlinePremiumTruth) -> T,
            onOnlineUnavailable: (OnlineUnavailableReason) -> T,
        ): T = onOnlineUnavailable(reason)

        override fun toString(): String = "ProductState.OnlineUnavailable($reason)"
    }

    object ProductStateReducer {
        fun local(): ProductState = LocalState

        fun online(
            dto: EntitlementV1Dto,
            expectedSubject: String,
            now: Instant,
        ): ProductState {
            val projection = dto.validated(expectedSubject).getOrElse {
                return OnlineUnavailableState(OnlineUnavailableReason.INCOMPATIBLE_RESPONSE)
            }
            if (now.isBefore(projection.evaluatedAt)) {
                return OnlineUnavailableState(OnlineUnavailableReason.INCOMPATIBLE_RESPONSE)
            }
            if (!now.isBefore(projection.refreshAfter)) {
                return OnlineUnavailableState(OnlineUnavailableReason.STALE)
            }
            return when (projection.tier) {
                EntitlementTierDto.FREE -> OnlineFreeState(OnlineFreeTruth.from(projection))
                EntitlementTierDto.PREMIUM -> OnlinePremiumState(OnlinePremiumTruth.from(projection))
            }
        }

        fun unavailable(reason: OnlineUnavailableReason): ProductState = OnlineUnavailableState(reason)
    }
}

class HouseAdEligibilityCapability internal constructor(
    private val validFrom: Instant,
    val validUntil: Instant,
) {
    fun isActiveAt(now: Instant): Boolean = !now.isBefore(validFrom) && now.isBefore(validUntil)

    override fun toString(): String = "HouseAdEligibilityCapability(redacted)"
}

class OnlineFreeCapabilities internal constructor(
    val podcastSubscriptions: Boolean,
    val transcriptEmail: Boolean,
    val topicCorpus: Boolean,
) {
    override fun toString(): String = "OnlineFreeCapabilities(redacted)"
}

class OnlinePremiumCapabilities internal constructor(
    val podcastSubscriptions: Boolean,
    val transcriptEmail: Boolean,
    val mobileAdFree: Boolean,
    val topicCorpus: Boolean,
) {
    override fun toString(): String = "OnlinePremiumCapabilities(redacted)"
}

class EntitlementTruth internal constructor(
    val subject: String,
    val source: EntitlementSourceKindDto,
    val entitlementRevision: Long,
    val flagsRevision: Long,
    val evaluatedAt: Instant,
    val validUntil: Instant,
) {
    override fun toString(): String = "EntitlementTruth(redacted)"
}

class OnlineFreeTruth private constructor(
    val entitlement: EntitlementTruth,
    val capabilities: OnlineFreeCapabilities,
    val houseAds: HouseAdEligibilityCapability?,
) {
    override fun toString(): String = "OnlineFreeTruth(redacted)"

    internal companion object {
        fun from(projection: EntitlementProjection): OnlineFreeTruth {
            check(projection.tier == EntitlementTierDto.FREE)
            return OnlineFreeTruth(
                entitlement = projection.toTruth(),
                capabilities = OnlineFreeCapabilities(
                    podcastSubscriptions = projection.capabilities.podcastSubscriptions,
                    transcriptEmail = projection.capabilities.transcriptEmail,
                    topicCorpus = projection.capabilities.topicCorpus,
                ),
                houseAds = projection.capabilities.adPolicy.takeIf { it == AdPolicyDto.HOUSE }?.let {
                    HouseAdEligibilityCapability(projection.evaluatedAt, projection.refreshAfter)
                },
            )
        }
    }
}

class OnlinePremiumTruth private constructor(
    val entitlement: EntitlementTruth,
    val capabilities: OnlinePremiumCapabilities,
) {
    override fun toString(): String = "OnlinePremiumTruth(redacted)"

    internal companion object {
        fun from(projection: EntitlementProjection): OnlinePremiumTruth {
            check(projection.tier == EntitlementTierDto.PREMIUM)
            return OnlinePremiumTruth(
                entitlement = projection.toTruth(),
                capabilities = OnlinePremiumCapabilities(
                    podcastSubscriptions = projection.capabilities.podcastSubscriptions,
                    transcriptEmail = projection.capabilities.transcriptEmail,
                    mobileAdFree = projection.capabilities.mobileAdFree,
                    topicCorpus = projection.capabilities.topicCorpus,
                ),
            )
        }
    }
}

private fun EntitlementProjection.toTruth(): EntitlementTruth = EntitlementTruth(
    subject = subject,
    source = source,
    entitlementRevision = entitlementRevision,
    flagsRevision = flagsRevision,
    evaluatedAt = evaluatedAt,
    validUntil = refreshAfter,
)

enum class OnlineUnavailableReason {
    OFFLINE,
    UNAUTHORIZED,
    STALE,
    INCOMPATIBLE_RESPONSE,
}
