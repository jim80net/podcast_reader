package net.jim80.podcastreader.ui

import java.time.Instant
import net.jim80.podcastreader.core.ads.HouseAdCreative
import net.jim80.podcastreader.core.ads.HouseAdCta
import net.jim80.podcastreader.core.ads.HouseAdPlacement
import net.jim80.podcastreader.core.ads.HouseInventory
import net.jim80.podcastreader.core.premium.AdPolicyDto
import net.jim80.podcastreader.core.premium.EntitlementCapabilitiesDto
import net.jim80.podcastreader.core.premium.EntitlementProjection
import net.jim80.podcastreader.core.premium.EntitlementSourceKindDto
import net.jim80.podcastreader.core.premium.EntitlementTierDto
import net.jim80.podcastreader.core.premium.OnlineUnavailableReason
import net.jim80.podcastreader.core.premium.ProductState
import org.junit.Assert.assertNull
import org.junit.Assert.assertSame
import org.junit.Assert.assertTrue
import org.junit.Test

class PodcastReaderUiStateTest {
    private val now = Instant.parse("2026-08-03T00:00:00Z")

    @Test
    fun localPremiumAndUnavailableNeverProjectHouseSlots() {
        val inventory = inventory(HouseAdPlacement.LIBRARY)
        listOf(
            ProductState.Local,
            ProductState.OnlinePremium(projection(EntitlementTierDto.PREMIUM, AdPolicyDto.NONE, adFree = true)),
            ProductState.OnlineUnavailable(OnlineUnavailableReason.OFFLINE),
        ).forEach { productState ->
            val state = PodcastReaderUiState.project(productState, now, true, libraryInventory = inventory)
            assertNull(state.libraryInventory)
            assertNull(state.jobsInventory)
        }
    }

    @Test
    fun onlyFreshOnlineFreeHouseInventoryMountsInItsEchoedPlacement() {
        val library = inventory(HouseAdPlacement.LIBRARY)
        val jobs = inventory(HouseAdPlacement.JOBS)
        val free = ProductState.OnlineFree(projection(EntitlementTierDto.FREE, AdPolicyDto.HOUSE))

        val state = PodcastReaderUiState.project(free, now, true, libraryInventory = library, jobsInventory = jobs)
        assertSame(library, state.libraryInventory)
        assertSame(jobs, state.jobsInventory)

        val swapped = PodcastReaderUiState.project(free, now, true, libraryInventory = jobs, jobsInventory = library)
        assertNull(swapped.libraryInventory)
        assertNull(swapped.jobsInventory)
    }

    @Test
    fun staleTruthAndExpiredInventoryCollapseBeforeCompose() {
        val stale = ProductState.OnlineFree(
            projection(EntitlementTierDto.FREE, AdPolicyDto.HOUSE, refreshAfter = now),
        )
        assertNull(PodcastReaderUiState.project(stale, now, true, libraryInventory = inventory()).libraryInventory)

        val fresh = ProductState.OnlineFree(projection(EntitlementTierDto.FREE, AdPolicyDto.HOUSE))
        assertNull(
            PodcastReaderUiState.project(
                fresh,
                now,
                true,
                libraryInventory = inventory(expiresAt = now),
            ).libraryInventory,
        )
    }

    @Test
    fun accountCopyKeepsLocalFreePremiumAndUnavailableDistinct() {
        assertTrue(PodcastReaderUiState.project(ProductState.Local, now, false).account is AccountUiState.Local)
        assertTrue(
            PodcastReaderUiState.project(
                ProductState.OnlineFree(projection(EntitlementTierDto.FREE, AdPolicyDto.NONE)),
                now,
                true,
            ).account is AccountUiState.OnlineFree,
        )
        assertTrue(
            PodcastReaderUiState.project(
                ProductState.OnlinePremium(projection(EntitlementTierDto.PREMIUM, AdPolicyDto.NONE, adFree = true)),
                now,
                true,
            ).account is AccountUiState.OnlinePremium,
        )
        assertTrue(
            PodcastReaderUiState.project(
                ProductState.OnlineUnavailable(OnlineUnavailableReason.STALE),
                now,
                true,
            ).account is AccountUiState.OnlineUnavailable,
        )
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

    private fun inventory(
        placement: HouseAdPlacement = HouseAdPlacement.LIBRARY,
        expiresAt: Instant = now.plusSeconds(60),
    ) = HouseInventory(
        placement = placement,
        inventoryRevision = 1,
        expiresAt = expiresAt,
        items = listOf(
            HouseAdCreative(
                id = "ad_test",
                revision = 1,
                title = "Title",
                body = "Body",
                cta = HouseAdCta.fromContract("https://example.com").getOrThrow(),
            ),
        ),
    )
}
