package net.jim80.podcastreader.core.ads

import java.time.Instant
import net.jim80.podcastreader.core.premium.PremiumFailure

sealed interface HouseInventoryResult {
    data class Success(val inventory: HouseInventory) : HouseInventoryResult
    data object Empty : HouseInventoryResult
    data class Failure(val failure: PremiumFailure) : HouseInventoryResult
}

interface HouseInventoryApi {
    fun fetch(
        placement: HouseAdPlacement,
        now: Instant,
        entitlementValidUntil: Instant,
        requestId: String,
    ): HouseInventoryResult
}

fun interface HouseAdCtaOpener {
    fun open(cta: HouseAdCta): Result<Unit>
}

class HouseAdRepository(
    private val eligibility: EligibleHouseAds,
    private val api: HouseInventoryApi,
) {
    private val inventory = mutableMapOf<HouseAdPlacement, HouseInventory>()

    @Synchronized
    fun refresh(placement: HouseAdPlacement, now: Instant, requestId: String): HouseInventoryResult {
        if (!now.isBefore(eligibility.validUntil)) {
            inventory.clear()
            return HouseInventoryResult.Empty
        }
        return when (val result = api.fetch(placement, now, eligibility.validUntil, requestId)) {
            is HouseInventoryResult.Success -> result.also { inventory[placement] = it.inventory }
            HouseInventoryResult.Empty -> result.also { inventory.remove(placement) }
            is HouseInventoryResult.Failure -> result.also { inventory.clear() }
        }
    }

    @Synchronized
    fun current(placement: HouseAdPlacement, now: Instant): HouseInventory? {
        val value = inventory[placement] ?: return null
        if (!now.isBefore(eligibility.validUntil) || !now.isBefore(value.expiresAt)) {
            inventory.clear()
            return null
        }
        return value
    }

    @Synchronized
    fun clear() = inventory.clear()
}
