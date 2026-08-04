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
    private val clock: () -> Instant,
) {
    private val inventory = mutableMapOf<HouseAdPlacement, HouseInventory>()
    private val refreshGenerations = mutableMapOf<HouseAdPlacement, Long>()
    private var clearGeneration = 0L

    fun refresh(placement: HouseAdPlacement, now: Instant, requestId: String): HouseInventoryResult {
        val ticket = synchronized(this) {
            if (!now.isBefore(eligibility.validUntil)) {
                clearLocked()
                return HouseInventoryResult.Empty
            }
            val placementGeneration = refreshGenerations.getOrDefault(placement, 0L) + 1L
            refreshGenerations[placement] = placementGeneration
            RefreshTicket(clearGeneration, placementGeneration)
        }
        val result = api.fetch(placement, now, eligibility.validUntil, requestId)
        return synchronized(this) {
            if (
                ticket.clearGeneration != clearGeneration ||
                refreshGenerations[placement] != ticket.placementGeneration
            ) {
                return@synchronized HouseInventoryResult.Empty
            }
            if (!clock().isBefore(eligibility.validUntil)) {
                clearLocked()
                return@synchronized HouseInventoryResult.Empty
            }
            when (result) {
                is HouseInventoryResult.Success -> result.also { inventory[placement] = it.inventory }
                HouseInventoryResult.Empty -> result.also { inventory.remove(placement) }
                is HouseInventoryResult.Failure -> result.also { clearLocked() }
            }
        }
    }

    @Synchronized
    fun current(placement: HouseAdPlacement, now: Instant): HouseInventory? {
        if (!now.isBefore(eligibility.validUntil)) {
            clearLocked()
            return null
        }
        val value = inventory[placement] ?: return null
        if (!now.isBefore(value.expiresAt)) {
            clearLocked()
            return null
        }
        return value
    }

    @Synchronized
    fun clear() = clearLocked()

    private fun clearLocked() {
        clearGeneration += 1L
        refreshGenerations.clear()
        inventory.clear()
    }

    private data class RefreshTicket(
        val clearGeneration: Long,
        val placementGeneration: Long,
    )
}
