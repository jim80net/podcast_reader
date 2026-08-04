package net.jim80.podcastreader.ui

import java.time.Instant
import net.jim80.podcastreader.core.ads.HouseAdCreative
import net.jim80.podcastreader.core.ads.HouseAdCta
import net.jim80.podcastreader.core.ads.HouseAdPlacement
import net.jim80.podcastreader.core.ads.HouseInventory
import net.jim80.podcastreader.core.premium.DeviceAuthorizationSession
import net.jim80.podcastreader.core.premium.DeviceCode
import net.jim80.podcastreader.core.premium.OnlineUnavailableReason
import net.jim80.podcastreader.core.premium.PremiumOrigin
import net.jim80.podcastreader.core.premium.UserCode
import net.jim80.podcastreader.runtime.EngineRuntimeState
import net.jim80.podcastreader.runtime.PodcastReaderRuntimeSnapshot
import net.jim80.podcastreader.support.FixtureProductStates
import org.junit.Assert.assertNull
import org.junit.Assert.assertSame
import org.junit.Assert.assertTrue
import org.junit.Test

class PodcastReaderUiStateTest {
    private val fixtures = FixtureProductStates(::fixture)
    private val now = fixtures.now

    @Test
    fun localPremiumAndUnavailableNeverProjectHouseSlots() {
        val inventory = inventory(HouseAdPlacement.LIBRARY)
        listOf(
            PodcastReaderRuntimeSnapshot.local(),
            PodcastReaderRuntimeSnapshot.online(fixtures.premium(), libraryInventory = inventory),
            PodcastReaderRuntimeSnapshot.online(
                fixtures.unavailable(OnlineUnavailableReason.OFFLINE),
                libraryInventory = inventory,
            ),
        ).forEach { snapshot ->
            val state = PodcastReaderUiState.project(
                snapshot,
                now,
            )
            assertNull(state.libraryInventory)
            assertNull(state.jobsInventory)
        }
    }

    @Test
    fun onlyFreshOnlineFreeHouseInventoryMountsInItsEchoedPlacement() {
        val library = inventory(HouseAdPlacement.LIBRARY)
        val jobs = inventory(HouseAdPlacement.JOBS)
        val free = fixtures.free(houseAds = true)

        val state = PodcastReaderUiState.project(
            PodcastReaderRuntimeSnapshot.online(free, libraryInventory = library, jobsInventory = jobs),
            now,
        )
        assertSame(library, state.libraryInventory)
        assertSame(jobs, state.jobsInventory)

        val swapped = PodcastReaderUiState.project(
            PodcastReaderRuntimeSnapshot.online(free, libraryInventory = jobs, jobsInventory = library),
            now,
        )
        assertNull(swapped.libraryInventory)
        assertNull(swapped.jobsInventory)
    }

    @Test
    fun staleTruthAndExpiredInventoryCollapseBeforeCompose() {
        val stale = fixtures.free(houseAds = true, at = Instant.parse("2026-08-02T00:05:00Z"))
        assertNull(
            PodcastReaderUiState.project(
                PodcastReaderRuntimeSnapshot.online(stale, libraryInventory = inventory()),
                now,
            ).libraryInventory,
        )

        val fresh = fixtures.free(houseAds = true)
        assertNull(
            PodcastReaderUiState.project(
                PodcastReaderRuntimeSnapshot.online(
                    fresh,
                    libraryInventory = inventory(expiresAt = now),
                ),
                now,
            ).libraryInventory,
        )
    }

    @Test
    fun accountCopyKeepsLocalFreePremiumAndUnavailableDistinct() {
        assertTrue(
            PodcastReaderUiState.project(PodcastReaderRuntimeSnapshot.local(), now).account is AccountUiState.Local,
        )
        assertTrue(
            PodcastReaderUiState.project(
                PodcastReaderRuntimeSnapshot.online(fixtures.free()),
                now,
            ).account is AccountUiState.OnlineFree,
        )
        assertTrue(
            PodcastReaderUiState.project(
                PodcastReaderRuntimeSnapshot.online(fixtures.premium()),
                now,
            ).account is AccountUiState.OnlinePremium,
        )
        assertTrue(
            PodcastReaderUiState.project(
                PodcastReaderRuntimeSnapshot.online(fixtures.unavailable(OnlineUnavailableReason.STALE)),
                now,
            ).account is AccountUiState.OnlineUnavailable,
        )
    }

    @Test
    fun projectionDoesNotPreemptTheRuntimeWhenAuthorizationTimePasses() {
        val session = DeviceAuthorizationSession(
            origin = PremiumOrigin.fromTrustedConfiguration("https://premium.example.test").getOrThrow(),
            deviceCode = DeviceCode.fromAuthorization("device_code_abcdefghijklmnopqrstuvwxyz").getOrThrow(),
            userCode = UserCode.fromAuthorization("K4AA-7BCD").getOrThrow(),
            verificationUri = "https://premium.example.test/device",
            expiresAt = now.minusSeconds(1),
            pollIntervalSeconds = 5,
            nextPollAt = now.minusSeconds(1),
        )

        val account = PodcastReaderUiState.project(
            PodcastReaderRuntimeSnapshot.authorizing(EngineRuntimeState.PAIRED, session),
            now,
        ).account

        assertTrue(account is AccountUiState.Authorizing)
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

    private fun fixture(name: String): String = requireNotNull(javaClass.classLoader?.getResource(name)) {
        "missing backend-owned fixture $name"
    }.readText()
}
