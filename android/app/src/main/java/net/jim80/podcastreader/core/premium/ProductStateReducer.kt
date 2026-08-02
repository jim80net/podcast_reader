package net.jim80.podcastreader.core.premium

import java.time.Instant

sealed interface ProductState {
    data object Local : ProductState

    data class OnlineFree(val entitlement: EntitlementProjection) : ProductState

    data class OnlinePremium(val entitlement: EntitlementProjection) : ProductState

    data class OnlineUnavailable(val reason: OnlineUnavailableReason) : ProductState
}

enum class OnlineUnavailableReason {
    OFFLINE,
    UNAUTHORIZED,
    STALE,
    INCOMPATIBLE_RESPONSE,
}

object ProductStateReducer {
    fun local(): ProductState = ProductState.Local

    fun online(
        dto: EntitlementV1Dto,
        expectedSubject: String,
        now: Instant,
    ): ProductState {
        val projection = dto.validated(expectedSubject).getOrElse {
            return ProductState.OnlineUnavailable(OnlineUnavailableReason.INCOMPATIBLE_RESPONSE)
        }
        if (now.isBefore(projection.evaluatedAt)) {
            return ProductState.OnlineUnavailable(OnlineUnavailableReason.INCOMPATIBLE_RESPONSE)
        }
        if (!now.isBefore(projection.refreshAfter)) {
            return ProductState.OnlineUnavailable(OnlineUnavailableReason.STALE)
        }
        return when (projection.tier) {
            EntitlementTierDto.FREE -> ProductState.OnlineFree(projection)
            EntitlementTierDto.PREMIUM -> ProductState.OnlinePremium(projection)
        }
    }

    fun unavailable(reason: OnlineUnavailableReason): ProductState = ProductState.OnlineUnavailable(reason)
}
