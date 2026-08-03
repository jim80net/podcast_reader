package net.jim80.podcastreader.ui

import java.time.Instant
import net.jim80.podcastreader.core.ads.HouseAdCreative
import net.jim80.podcastreader.core.ads.HouseAdCta
import net.jim80.podcastreader.core.ads.HouseAdPlacement
import net.jim80.podcastreader.core.ads.HouseInventory
import net.jim80.podcastreader.core.premium.OnlineUnavailableReason
import net.jim80.podcastreader.support.FixtureProductStates
import org.junit.Assert.assertNull
import org.junit.Assert.assertSame
import org.junit.Assert.assertTrue
import org.junit.Test

class PodcastReaderUiStateTest {
    private val now = FixtureProductStates.now

    @Test
    fun localPremiumAndUnavailableNeverProjectHouseSlots() {
        val inventory = inventory(HouseAdPlacement.LIBRARY)
        listOf(
            FixtureProductStates.local(),
            FixtureProductStates.premium(),
            FixtureProductStates.unavailable(OnlineUnavailableReason.OFFLINE),
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
        val free = FixtureProductStates.free(houseAds = true)

        val state = PodcastReaderUiState.project(free, now, true, libraryInventory = library, jobsInventory = jobs)
        assertSame(library, state.libraryInventory)
        assertSame(jobs, state.jobsInventory)

        val swapped = PodcastReaderUiState.project(free, now, true, libraryInventory = jobs, jobsInventory = library)
        assertNull(swapped.libraryInventory)
        assertNull(swapped.jobsInventory)
    }

    @Test
    fun staleTruthAndExpiredInventoryCollapseBeforeCompose() {
        val stale = FixtureProductStates.free(houseAds = true, at = Instant.parse("2026-08-02T00:05:00Z"))
        assertNull(PodcastReaderUiState.project(stale, now, true, libraryInventory = inventory()).libraryInventory)

        val fresh = FixtureProductStates.free(houseAds = true)
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
        assertTrue(PodcastReaderUiState.project(FixtureProductStates.local(), now, false).account is AccountUiState.Local)
        assertTrue(
            PodcastReaderUiState.project(
                FixtureProductStates.free(),
                now,
                true,
            ).account is AccountUiState.OnlineFree,
        )
        assertTrue(
            PodcastReaderUiState.project(
                FixtureProductStates.premium(),
                now,
                true,
            ).account is AccountUiState.OnlinePremium,
        )
        assertTrue(
            PodcastReaderUiState.project(
                FixtureProductStates.unavailable(OnlineUnavailableReason.STALE),
                now,
                true,
            ).account is AccountUiState.OnlineUnavailable,
        )
    }

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
