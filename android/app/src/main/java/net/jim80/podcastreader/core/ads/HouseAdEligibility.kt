package net.jim80.podcastreader.core.ads

import java.time.Instant
import net.jim80.podcastreader.core.premium.AdPolicyDto
import net.jim80.podcastreader.core.premium.ProductState

class EligibleHouseAds internal constructor(val validUntil: Instant)

object HouseAdRuntimeGate {
    fun <T> create(
        state: ProductState,
        now: Instant,
        factory: (EligibleHouseAds) -> T,
    ): T? {
        val onlineFree = state as? ProductState.OnlineFree ?: return null
        val entitlement = onlineFree.entitlement
        if (
            entitlement.capabilities.adPolicy != AdPolicyDto.HOUSE ||
            entitlement.capabilities.mobileAdFree ||
            now.isBefore(entitlement.evaluatedAt) ||
            !now.isBefore(entitlement.refreshAfter)
        ) {
            return null
        }
        return factory(EligibleHouseAds(entitlement.refreshAfter))
    }
}
