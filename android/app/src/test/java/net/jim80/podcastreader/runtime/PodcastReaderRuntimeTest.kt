package net.jim80.podcastreader.runtime

import java.time.Instant
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.test.StandardTestDispatcher
import kotlinx.coroutines.test.advanceUntilIdle
import kotlinx.coroutines.test.runTest
import net.jim80.podcastreader.core.premium.ConnectedPremiumSession
import net.jim80.podcastreader.core.premium.OnlineUnavailableReason
import net.jim80.podcastreader.core.premium.PremiumAccountCredentials
import net.jim80.podcastreader.core.premium.PremiumOrigin
import net.jim80.podcastreader.core.premium.PremiumRefreshToken
import net.jim80.podcastreader.core.premium.PremiumRestoreResult
import net.jim80.podcastreader.core.premium.ProductState.ProductStateReducer
import net.jim80.podcastreader.ui.AccountUiState
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

@OptIn(ExperimentalCoroutinesApi::class)
class PodcastReaderRuntimeTest {
    private val now = Instant.parse("2026-08-03T18:30:00Z")

    @Test
    fun pairedEngineWithNoAccountConstructsZeroPremiumResources() = runTest {
        var premiumConstructions = 0
        var engineReads = 0
        val records = TestPremiumRecords(Result.success(null))
        val runtime = PodcastReaderRuntime(
            scope = this,
            workDispatcher = StandardTestDispatcher(testScheduler),
            engineRecords = EngineRecordProbe {
                engineReads += 1
                Result.success(true)
            },
            premiumRecords = records,
            connectedFactory = ConnectedPremiumSessionFactory {
                premiumConstructions += 1
                error("local mode constructed premium resources")
            },
            now = { now },
        )

        assertTrue(runtime.uiState.value.account is AccountUiState.Bootstrapping)
        runtime.foreground()
        advanceUntilIdle()
        runtime.foreground() // Activity recreation must not duplicate work.
        advanceUntilIdle()

        assertEquals(0, premiumConstructions)
        assertEquals(1, engineReads)
        assertEquals(1, records.loadCount)
        assertEquals(EngineRuntimeState.PAIRED, runtime.snapshotForTest().engine)
        assertTrue(runtime.uiState.value.account is AccountUiState.Local)
    }

    @Test
    fun premiumReadFailureIsUnavailableAndNeverInferredAsFree() = runTest {
        var premiumConstructions = 0
        val runtime = runtime(
            accountResult = Result.failure(IllegalStateException("unreadable")),
            factory = ConnectedPremiumSessionFactory {
                premiumConstructions += 1
                error("unreadable record constructed premium resources")
            },
        )

        runtime.foreground()
        advanceUntilIdle()

        assertEquals(0, premiumConstructions)
        assertTrue(runtime.uiState.value.account is AccountUiState.OnlineUnavailable)
    }

    @Test
    fun presentAccountCreatesOneConnectedSessionAndProjectsItsReducerTruth() = runTest {
        var premiumConstructions = 0
        val runtime = runtime(
            accountResult = Result.success(account()),
            factory = ConnectedPremiumSessionFactory {
                premiumConstructions += 1
                CompletedSession(
                    PremiumRestoreResult.Online(
                        ProductStateReducer.unavailable(OnlineUnavailableReason.OFFLINE),
                    ),
                )
            },
        )

        runtime.foreground()
        advanceUntilIdle()

        assertEquals(1, premiumConstructions)
        assertTrue(runtime.uiState.value.account is AccountUiState.OnlineUnavailable)
    }

    @Test
    fun signOutRejectsLateRestoreCompletionAndClearsOnlyPremiumAccess() = runTest {
        val delayed = CompletableDeferred<PremiumRestoreResult>()
        val records = TestPremiumRecords(Result.success(account()))
        val dispatcher = StandardTestDispatcher(testScheduler)
        val runtime = PodcastReaderRuntime(
            scope = this,
            workDispatcher = dispatcher,
            engineRecords = EngineRecordProbe { Result.success(true) },
            premiumRecords = records,
            connectedFactory = ConnectedPremiumSessionFactory { DelayedSession(delayed) },
            now = { now },
        )

        runtime.foreground()
        testScheduler.runCurrent()
        runtime.dispatch(PodcastReaderRuntimeEvent.SignOut)
        delayed.complete(
            PremiumRestoreResult.Online(
                ProductStateReducer.unavailable(OnlineUnavailableReason.OFFLINE),
            ),
        )
        advanceUntilIdle()

        assertTrue(runtime.uiState.value.account is AccountUiState.Local)
        assertEquals(1, records.clearCount)
        assertEquals(EngineRuntimeState.PAIRED, runtime.snapshotForTest().engine)
    }

    @Test
    fun backgroundRejectsThePreviousForegroundGeneration() = runTest {
        val delayed = CompletableDeferred<PremiumRestoreResult>()
        val runtime = runtime(
            accountResult = Result.success(account()),
            factory = ConnectedPremiumSessionFactory { DelayedSession(delayed) },
        )

        runtime.foreground()
        testScheduler.runCurrent()
        runtime.background()
        delayed.complete(
            PremiumRestoreResult.Online(
                ProductStateReducer.unavailable(OnlineUnavailableReason.OFFLINE),
            ),
        )
        advanceUntilIdle()

        assertTrue(runtime.uiState.value.account is AccountUiState.Bootstrapping)
    }

    private fun kotlinx.coroutines.test.TestScope.runtime(
        enginePresence: Result<Boolean> = Result.success(false),
        accountResult: Result<PremiumAccountCredentials?>,
        factory: ConnectedPremiumSessionFactory,
    ): PodcastReaderRuntime = PodcastReaderRuntime(
        scope = this,
        workDispatcher = StandardTestDispatcher(testScheduler),
        engineRecords = EngineRecordProbe { enginePresence },
        premiumRecords = TestPremiumRecords(accountResult),
        connectedFactory = factory,
        now = { now },
    )

    private fun account() = PremiumAccountCredentials(
        PremiumOrigin.fromTrustedConfiguration("https://premium.example.test").getOrThrow(),
        PremiumRefreshToken.fromAuthorization("refresh_token_abcdefghijklmnopqrstuvwxyz").getOrThrow(),
    )
}

private class TestPremiumRecords(
    private val result: Result<PremiumAccountCredentials?>,
) : PremiumAccountRecordAccess {
    var loadCount = 0
    var clearCount = 0

    override fun load(): Result<PremiumAccountCredentials?> = result.also { loadCount += 1 }
    override fun clear(): Result<Unit> = Result.success(Unit).also { clearCount += 1 }
}

private class CompletedSession(
    private val result: PremiumRestoreResult,
) : ConnectedPremiumSession {
    override suspend fun restore(now: Instant, requestId: String): PremiumRestoreResult = result
    override suspend fun signOut(requestId: String) = Unit
}

private class DelayedSession(
    private val result: CompletableDeferred<PremiumRestoreResult>,
) : ConnectedPremiumSession {
    override suspend fun restore(now: Instant, requestId: String): PremiumRestoreResult = result.await()
    override suspend fun signOut(requestId: String) = Unit
}
