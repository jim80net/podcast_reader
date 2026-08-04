package net.jim80.podcastreader.core.ads

import java.time.Instant
import java.util.concurrent.CountDownLatch
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit
import net.jim80.podcastreader.core.premium.PremiumFailure
import net.jim80.podcastreader.core.premium.PremiumFailureCategory
import net.jim80.podcastreader.core.premium.OnlineUnavailableReason
import net.jim80.podcastreader.support.FixtureProductStates
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Test

class HouseAdEligibilityTest {
    private val fixtures = FixtureProductStates(::fixture)
    private val now = fixtures.now

    @Test
    fun localPremiumUnavailableAndIneligibleFreeNeverConstructTheRepository() {
        val states = listOf(
            fixtures.local(),
            fixtures.premium(),
            fixtures.unavailable(OnlineUnavailableReason.OFFLINE),
            fixtures.free(),
            fixtures.free(houseAds = true, at = Instant.parse("2026-08-02T00:05:00Z")),
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
        val state = fixtures.free(houseAds = true)

        val marker = HouseAdRuntimeGate.create(state, now) { eligibility ->
            constructions += 1
            eligibility.validUntil
        }

        assertEquals(Instant.parse("2026-08-02T00:05:00Z"), marker)
        assertEquals(1, constructions)
    }

    @Test
    fun repositoryMakesNoCallAfterTruthExpiresAndEvictsOnFailure() {
        val api = RecordingInventoryApi(successInventory())
        val repository = HouseAdRepository(EligibleHouseAds(now.plusSeconds(300)), api) { now }
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

    @Test
    fun blockedFetchDoesNotHoldTheRepositoryMonitorAndCannotCommitAfterClear() {
        val started = CountDownLatch(1)
        val release = CountDownLatch(1)
        val api = BlockingInventoryApi(successInventory(), started, release)
        val repository = HouseAdRepository(EligibleHouseAds(now.plusSeconds(300)), api) { now }
        assertTrue(repository.refresh(HouseAdPlacement.LIBRARY, now, "seed") is HouseInventoryResult.Success)
        val executor = Executors.newFixedThreadPool(3)
        val refresh = executor.submit<HouseInventoryResult> {
            repository.refresh(HouseAdPlacement.LIBRARY, now.plusSeconds(1), "blocked")
        }

        try {
            assertTrue(started.await(1, TimeUnit.SECONDS))
            val current = executor.submit<HouseInventory?> {
                repository.current(HouseAdPlacement.LIBRARY, now.plusSeconds(1))
            }
            assertNotNull(current.get(1, TimeUnit.SECONDS))
            val cleared = executor.submit<Unit> { repository.clear() }
            cleared.get(1, TimeUnit.SECONDS)
            assertNull(repository.current(HouseAdPlacement.LIBRARY, now.plusSeconds(1)))
        } finally {
            release.countDown()
            executor.shutdown()
        }

        assertEquals(HouseInventoryResult.Empty, refresh.get(1, TimeUnit.SECONDS))
        assertNull(repository.current(HouseAdPlacement.LIBRARY, now.plusSeconds(1)))
    }

    @Test
    fun blockedFetchCannotCommitAfterEligibilityExpires() {
        val validUntil = now.plusSeconds(300)
        var clockNow = now
        val started = CountDownLatch(1)
        val release = CountDownLatch(1)
        val api = BlockingInventoryApi(successInventory(), started, release)
        val repository = HouseAdRepository(EligibleHouseAds(validUntil), api) { clockNow }
        assertTrue(repository.refresh(HouseAdPlacement.LIBRARY, now, "seed") is HouseInventoryResult.Success)
        val executor = Executors.newSingleThreadExecutor()
        val refresh = executor.submit<HouseInventoryResult> {
            repository.refresh(HouseAdPlacement.LIBRARY, now.plusSeconds(1), "blocked-expiry")
        }

        try {
            assertTrue(started.await(1, TimeUnit.SECONDS))
            clockNow = validUntil
        } finally {
            release.countDown()
            executor.shutdown()
        }

        assertEquals(HouseInventoryResult.Empty, refresh.get(1, TimeUnit.SECONDS))
        assertNull(repository.current(HouseAdPlacement.LIBRARY, validUntil))
    }

    private fun successInventory() = HouseInventoryResult.Success(
        HouseInventory(
            HouseAdPlacement.LIBRARY,
            1,
            now.plusSeconds(60),
            listOf(HouseAdCreative("ad_test", 1, "Title", "Body", HouseAdCta.fromContract("https://example.com").getOrThrow())),
        ),
    )

    private fun fixture(name: String): String = requireNotNull(javaClass.classLoader?.getResource(name)) {
        "missing backend-owned fixture $name"
    }.readText()
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

private class BlockingInventoryApi(
    private val result: HouseInventoryResult,
    private val started: CountDownLatch,
    private val release: CountDownLatch,
) : HouseInventoryApi {
    private var calls = 0

    @Synchronized
    override fun fetch(
        placement: HouseAdPlacement,
        now: Instant,
        entitlementValidUntil: Instant,
        requestId: String,
    ): HouseInventoryResult {
        calls += 1
        if (calls > 1) {
            started.countDown()
            check(release.await(5, TimeUnit.SECONDS)) { "blocked fetch was never released" }
        }
        return result
    }
}
