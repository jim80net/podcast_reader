package net.jim80.podcastreader.core.ads

import java.time.Instant
import net.jim80.podcastreader.core.premium.ProductState

class EligibleHouseAds internal constructor(val validUntil: Instant)

object HouseAdRuntimeGate {
    fun <T> create(
        state: ProductState,
        now: Instant,
        factory: (EligibleHouseAds) -> T,
    ): T? = state.fold(
        onLocal = { null },
        onOnlineFree = { truth ->
            truth.houseAds?.takeIf { it.isActiveAt(now) }?.let {
                factory(EligibleHouseAds(it.validUntil))
            }
        },
        onOnlinePremium = { null },
        onOnlineUnavailable = { null },
    )
}
