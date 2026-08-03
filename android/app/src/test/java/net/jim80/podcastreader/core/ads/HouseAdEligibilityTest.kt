package net.jim80.podcastreader.core.ads

import java.time.Instant
import net.jim80.podcastreader.core.premium.AdPolicyDto
import net.jim80.podcastreader.core.premium.EntitlementCapabilitiesDto
import net.jim80.podcastreader.core.premium.EntitlementProjection
import net.jim80.podcastreader.core.premium.EntitlementSourceKindDto
import net.jim80.podcastreader.core.premium.EntitlementTierDto
import net.jim80.podcastreader.core.premium.PremiumFailure
import net.jim80.podcastreader.core.premium.PremiumFailureCategory
import net.jim80.podcastreader.core.premium.ProductState
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class HouseAdEligibilityTest {
    private val now = Instant.parse("2026-08-03T00:00:00Z")

    @Test
    fun localPremiumUnavailableAndIneligibleFreeNeverConstructTheRepository() {
        val states = listOf(
            ProductState.Local,
            ProductState.OnlinePremium(projection(EntitlementTierDto.PREMIUM, AdPolicyDto.NONE, adFree = true)),
            ProductState.OnlineUnavailable(net.jim80.podcastreader.core.premium.OnlineUnavailableReason.OFFLINE),
            ProductState.OnlineFree(projection(EntitlementTierDto.FREE, AdPolicyDto.NONE)),
            ProductState.OnlineFree(projection(EntitlementTierDto.FREE, AdPolicyDto.HOUSE, refreshAfter = now)),
        )
        var constructions = 0

        states.forEach { state ->
            assertNull(HouseAdRuntimeGate.create(state, now) { constructions += 1 })
        }
        assertEquals(0, constructions)
    }

    @Test
    fun onlyFreshOnlineFreeHouseTruthConstructsOnce() {
        var constructions = 0
        val state = ProductState.OnlineFree(projection(EntitlementTierDto.FREE, AdPolicyDto.HOUSE))

        val marker = HouseAdRuntimeGate.create(state, now) { eligibility ->
            constructions += 1
            eligibility.validUntil
        }

        assertEquals(now.plusSeconds(300), marker)
        assertEquals(1, constructions)
    }

    @Test
    fun repositoryMakesNoCallAfterTruthExpiresAndEvictsOnFailure() {
        val api = RecordingInventoryApi(successInventory())
        val repository = HouseAdRepository(EligibleHouseAds(now.plusSeconds(300)), api)
        assertTrue(repository.refresh(HouseAdPlacement.LIBRARY, now, "r1") is HouseInventoryResult.Success)
        assertTrue(repository.current(HouseAdPlacement.LIBRARY, now) != null)

        api.result = HouseInventoryResult.Failure(
            PremiumFailure(PremiumFailureCategory.NETWORK, requestId = "r2"),
        )
        repository.refresh(HouseAdPlacement.JOBS, now.plusSeconds(1), "r2")
        assertNull(repository.current(HouseAdPlacement.LIBRARY, now.plusSeconds(1)))

        api.result = successInventory()
        repository.refresh(HouseAdPlacement.LIBRARY, now.plusSeconds(2), "r-clear")
        repository.clear()
        assertNull(repository.current(HouseAdPlacement.LIBRARY, now.plusSeconds(2)))

        repository.refresh(HouseAdPlacement.LIBRARY, now.plusSeconds(300), "r3")
        assertEquals(3, api.calls)
    }

    private fun projection(
        tier: EntitlementTierDto,
        adPolicy: AdPolicyDto,
        adFree: Boolean = false,
        refreshAfter: Instant = now.plusSeconds(300),
    ) = EntitlementProjection(
        subject = "usr_fixture",
        tier = tier,
        source = if (tier == EntitlementTierDto.FREE) EntitlementSourceKindDto.NONE else EntitlementSourceKindDto.TEST_PURCHASE,
        entitlementRevision = 1,
        capabilities = EntitlementCapabilitiesDto(adPolicy, false, false, adFree, false),
        flagsRevision = 1,
        evaluatedAt = now,
        refreshAfter = refreshAfter,
    )

    private fun successInventory() = HouseInventoryResult.Success(
        HouseInventory(
            HouseAdPlacement.LIBRARY,
            1,
            now.plusSeconds(60),
            listOf(HouseAdCreative("ad_test", 1, "Title", "Body", HouseAdCta.fromContract("https://example.com").getOrThrow())),
        ),
    )
}

private class RecordingInventoryApi(initial: HouseInventoryResult) : HouseInventoryApi {
    var result = initial
    var calls = 0

    override fun fetch(
        placement: HouseAdPlacement,
        now: Instant,
        entitlementValidUntil: Instant,
        requestId: String,
    ): HouseInventoryResult = result.also { calls += 1 }
}
