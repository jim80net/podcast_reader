package net.jim80.podcastreader.runtime

import java.time.Instant
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.withContext
import kotlinx.coroutines.test.StandardTestDispatcher
import kotlinx.coroutines.test.advanceUntilIdle
import kotlinx.coroutines.test.runTest
import net.jim80.podcastreader.core.premium.AuthorizedPremiumTokens
import net.jim80.podcastreader.core.premium.ConnectedPremiumSession
import net.jim80.podcastreader.core.premium.DeviceAuthorizationSession
import net.jim80.podcastreader.core.premium.DeviceAuthorizationTransition
import net.jim80.podcastreader.core.premium.DeviceCode
import net.jim80.podcastreader.core.premium.OnlineUnavailableReason
import net.jim80.podcastreader.core.premium.PremiumAccessToken
import net.jim80.podcastreader.core.premium.PremiumAccountCredentials
import net.jim80.podcastreader.core.premium.PremiumFailure
import net.jim80.podcastreader.core.premium.PremiumFailureCategory
import net.jim80.podcastreader.core.premium.PremiumOrigin
import net.jim80.podcastreader.core.premium.PremiumRefreshToken
import net.jim80.podcastreader.core.premium.PremiumRestoreResult
import net.jim80.podcastreader.core.premium.ProductState.ProductStateReducer
import net.jim80.podcastreader.core.premium.UserCode
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
        var accountConnectionConstructions = 0
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
            accountConnectionFactory = PremiumAccountConnectionFactory {
                accountConnectionConstructions += 1
                error("local mode constructed account authorization resources")
            },
            now = { now },
        )

        assertTrue(runtime.uiState.value.account is AccountUiState.Bootstrapping)
        runtime.foreground()
        advanceUntilIdle()
        runtime.foreground() // Activity recreation must not duplicate work.
        advanceUntilIdle()

        assertEquals(0, premiumConstructions)
        assertEquals(0, accountConnectionConstructions)
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
        val delayedSession = DelayedSession(delayed)
        val records = TestPremiumRecords(Result.success(account()))
        val dispatcher = StandardTestDispatcher(testScheduler)
        val runtime = PodcastReaderRuntime(
            scope = this,
            workDispatcher = dispatcher,
            engineRecords = EngineRecordProbe { Result.success(true) },
            premiumRecords = records,
            connectedFactory = ConnectedPremiumSessionFactory { delayedSession },
            accountConnectionFactory = rejectingConnectionFactory(),
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

        assertTrue(delayedSession.restoreCompleted)
        assertTrue(runtime.uiState.value.account is AccountUiState.Local)
        assertEquals(1, records.clearCount)
        assertEquals(EngineRuntimeState.PAIRED, runtime.snapshotForTest().engine)
    }

    @Test
    fun signOutClearFailureNeverClaimsTheSurvivingRecordWasRemoved() = runTest {
        val records = TestPremiumRecords(
            result = Result.success(account()),
            clearResult = Result.failure(IllegalStateException("record survived")),
        )
        val runtime = PodcastReaderRuntime(
            scope = this,
            workDispatcher = StandardTestDispatcher(testScheduler),
            engineRecords = EngineRecordProbe { Result.success(true) },
            premiumRecords = records,
            connectedFactory = ConnectedPremiumSessionFactory {
                CompletedSession(
                    PremiumRestoreResult.Online(
                        ProductStateReducer.unavailable(OnlineUnavailableReason.OFFLINE),
                    ),
                )
            },
            accountConnectionFactory = rejectingConnectionFactory(),
            now = { now },
        )

        runtime.foreground()
        testScheduler.runCurrent()
        runtime.dispatch(PodcastReaderRuntimeEvent.SignOut)
        assertTrue(runtime.uiState.value.account is AccountUiState.Bootstrapping)
        advanceUntilIdle()

        assertEquals(1, records.clearCount)
        val unavailable = runtime.uiState.value.account as AccountUiState.OnlineUnavailable
        assertEquals(OnlineUnavailableReason.LOCAL_CREDENTIAL_STORAGE, unavailable.reason)
    }

    @Test
    fun lifecycleChangeCannotDiscardASignOutClearFailure() = runTest {
        val records = TestPremiumRecords(
            result = Result.success(account()),
            clearResult = Result.failure(IllegalStateException("record survived")),
        )
        val runtime = PodcastReaderRuntime(
            scope = this,
            workDispatcher = StandardTestDispatcher(testScheduler),
            engineRecords = EngineRecordProbe { Result.success(true) },
            premiumRecords = records,
            connectedFactory = ConnectedPremiumSessionFactory {
                CompletedSession(
                    PremiumRestoreResult.Online(
                        ProductStateReducer.unavailable(OnlineUnavailableReason.OFFLINE),
                    ),
                )
            },
            accountConnectionFactory = rejectingConnectionFactory(),
            now = { now },
        )

        runtime.foreground()
        testScheduler.runCurrent()
        runtime.dispatch(PodcastReaderRuntimeEvent.SignOut)
        runtime.background()
        runtime.foreground()
        advanceUntilIdle()

        assertEquals(1, records.clearCount)
        val unavailable = runtime.uiState.value.account as AccountUiState.OnlineUnavailable
        assertEquals(OnlineUnavailableReason.LOCAL_CREDENTIAL_STORAGE, unavailable.reason)
    }

    @Test
    fun olderSignOutCompletionCannotOverwriteTheLatestAttempt() = runTest {
        val firstSignOut = CompletableDeferred<Unit>()
        val session = DelayedSignOutSession(firstSignOut)
        val records = SequencedPremiumRecords(
            account = account(),
            clearResults = mutableListOf(
                Result.failure(IllegalStateException("latest clear failed")),
                Result.success(Unit),
            ),
        )
        val runtime = PodcastReaderRuntime(
            scope = this,
            workDispatcher = StandardTestDispatcher(testScheduler),
            engineRecords = EngineRecordProbe { Result.success(true) },
            premiumRecords = records,
            connectedFactory = ConnectedPremiumSessionFactory { session },
            accountConnectionFactory = rejectingConnectionFactory(),
            now = { now },
        )

        runtime.foreground()
        advanceUntilIdle()
        runtime.dispatch(PodcastReaderRuntimeEvent.SignOut)
        testScheduler.runCurrent()
        runtime.dispatch(PodcastReaderRuntimeEvent.SignOut)
        testScheduler.runCurrent()
        firstSignOut.complete(Unit)
        advanceUntilIdle()

        assertEquals(2, records.clearCount)
        val unavailable = runtime.uiState.value.account as AccountUiState.OnlineUnavailable
        assertEquals(OnlineUnavailableReason.LOCAL_CREDENTIAL_STORAGE, unavailable.reason)
    }

    @Test
    fun backgroundRejectsThePreviousForegroundGeneration() = runTest {
        val delayed = CompletableDeferred<PremiumRestoreResult>()
        val delayedSession = DelayedSession(delayed)
        val runtime = runtime(
            accountResult = Result.success(account()),
            factory = ConnectedPremiumSessionFactory { delayedSession },
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

        assertTrue(delayedSession.restoreCompleted)
        assertTrue(runtime.uiState.value.account is AccountUiState.Bootstrapping)
    }

    @Test
    fun invalidDevelopmentOriginConstructsNoPremiumConnection() = runTest {
        var connectionConstructions = 0
        val runtime = runtime(
            accountResult = Result.success(null),
            factory = ConnectedPremiumSessionFactory { error("unexpected restore") },
            connectionFactory = PremiumAccountConnectionFactory {
                connectionConstructions += 1
                error("invalid origin constructed a connection")
            },
        )

        runtime.foreground()
        advanceUntilIdle()
        runtime.dispatch(PodcastReaderRuntimeEvent.DevelopmentOriginChanged("http://unsafe.example.test"))
        runtime.dispatch(PodcastReaderRuntimeEvent.ConnectAccount)

        assertEquals(0, connectionConstructions)
        val local = runtime.uiState.value.account as AccountUiState.Local
        assertEquals(AccountConnectionIssue.INVALID_DEVELOPMENT_ORIGIN, local.connectionIssue)
    }

    @Test
    fun authorizedDeviceFlowInstallsThenValidatesThroughTheOwner() = runTest {
        val completed = CompletedSession(
            PremiumRestoreResult.Online(
                ProductStateReducer.unavailable(OnlineUnavailableReason.OFFLINE),
            ),
        )
        val connection = TestAccountConnection(
            initial = DeviceAuthorizationTransition.Waiting(authorizationSession(nextPollAt = now)),
            poll = DeviceAuthorizationTransition.Authorized(tokens()),
            completedSession = completed,
        )
        val runtime = runtime(
            accountResult = Result.success(null),
            factory = ConnectedPremiumSessionFactory { error("unexpected restore") },
            connectionFactory = PremiumAccountConnectionFactory { connection },
        )

        runtime.foreground()
        advanceUntilIdle()
        runtime.dispatch(PodcastReaderRuntimeEvent.DevelopmentOriginChanged("https://premium.example.test"))
        runtime.dispatch(PodcastReaderRuntimeEvent.ConnectAccount)
        advanceUntilIdle()

        assertEquals(1, connection.completeCount)
        assertEquals(1, connection.cancelCount)
        assertEquals(1, completed.validateCount)
        assertEquals(0, completed.restoreCount)
        assertTrue(runtime.uiState.value.account is AccountUiState.OnlineUnavailable)
    }

    @Test
    fun cancellingDeviceFlowClearsTheMemoryOnlyDraftAndReturnsLocal() = runTest {
        val connection = TestAccountConnection(
            initial = DeviceAuthorizationTransition.Waiting(
                authorizationSession(nextPollAt = now.plusSeconds(60)),
            ),
            poll = DeviceAuthorizationTransition.TooEarly,
            completedSession = CompletedSession(PremiumRestoreResult.Local),
        )
        val runtime = runtime(
            accountResult = Result.success(null),
            factory = ConnectedPremiumSessionFactory { error("unexpected restore") },
            connectionFactory = PremiumAccountConnectionFactory { connection },
        )

        runtime.foreground()
        advanceUntilIdle()
        runtime.dispatch(PodcastReaderRuntimeEvent.DevelopmentOriginChanged("https://premium.example.test"))
        runtime.dispatch(PodcastReaderRuntimeEvent.ConnectAccount)
        testScheduler.runCurrent()
        assertTrue(runtime.uiState.value.account is AccountUiState.Authorizing)

        runtime.dispatch(PodcastReaderRuntimeEvent.CancelAuthorization)
        advanceUntilIdle()

        assertEquals(1, connection.cancelCount)
        val local = runtime.uiState.value.account as AccountUiState.Local
        assertEquals("", local.developmentOriginDraft)
        assertEquals(false, local.developmentOriginValid)
    }

    @Test
    fun cancellingADeviceStartThatReturnsLateCancelsItsMintedSession() = runTest {
        lateinit var runtime: PodcastReaderRuntime
        val connection = TestAccountConnection(
            initial = DeviceAuthorizationTransition.Failed(
                PremiumFailure(
                    PremiumFailureCategory.NETWORK,
                    requestId = "unused",
                ),
            ),
            poll = DeviceAuthorizationTransition.TooEarly,
            completedSession = CompletedSession(PremiumRestoreResult.Local),
            onBegin = {
                runtime.dispatch(PodcastReaderRuntimeEvent.CancelAuthorization)
                DeviceAuthorizationTransition.Waiting(authorizationSession(nextPollAt = now.plusSeconds(5)))
            },
        )
        runtime = runtime(
            accountResult = Result.success(null),
            factory = ConnectedPremiumSessionFactory { error("unexpected restore") },
            connectionFactory = PremiumAccountConnectionFactory { connection },
        )

        runtime.foreground()
        advanceUntilIdle()
        runtime.dispatch(PodcastReaderRuntimeEvent.DevelopmentOriginChanged("https://premium.example.test"))
        runtime.dispatch(PodcastReaderRuntimeEvent.ConnectAccount)
        advanceUntilIdle()

        assertEquals(1, connection.cancelCount)
        assertTrue(runtime.uiState.value.account is AccountUiState.Local)
    }

    @Test
    fun cancellingDuringTooEarlyDelayPreventsAnotherPoll() = runTest {
        val connection = TestAccountConnection(
            initial = DeviceAuthorizationTransition.Waiting(authorizationSession(nextPollAt = now)),
            poll = DeviceAuthorizationTransition.TooEarly,
            completedSession = CompletedSession(PremiumRestoreResult.Local),
        )
        val runtime = runtime(
            accountResult = Result.success(null),
            factory = ConnectedPremiumSessionFactory { error("unexpected restore") },
            connectionFactory = PremiumAccountConnectionFactory { connection },
        )

        runtime.foreground()
        advanceUntilIdle()
        runtime.dispatch(PodcastReaderRuntimeEvent.DevelopmentOriginChanged("https://premium.example.test"))
        runtime.dispatch(PodcastReaderRuntimeEvent.ConnectAccount)
        testScheduler.runCurrent()
        assertEquals(1, connection.pollCount)

        runtime.dispatch(PodcastReaderRuntimeEvent.CancelAuthorization)
        advanceUntilIdle()

        assertEquals(1, connection.pollCount)
        assertEquals(1, connection.cancelCount)
    }

    @Test
    fun backgroundApprovalPersistsButDefersTruthValidationUntilForegroundRestore() = runTest {
        val records = TestPremiumRecords(Result.success(null))
        val completed = CompletedSession(
            PremiumRestoreResult.Online(
                ProductStateReducer.unavailable(OnlineUnavailableReason.OFFLINE),
            ),
        )
        val connection = TestAccountConnection(
            initial = DeviceAuthorizationTransition.Waiting(authorizationSession(nextPollAt = now)),
            poll = DeviceAuthorizationTransition.Authorized(tokens()),
            completedSession = completed,
            onComplete = { records.install(account()) },
        )
        val runtime = PodcastReaderRuntime(
            scope = this,
            workDispatcher = StandardTestDispatcher(testScheduler),
            engineRecords = EngineRecordProbe { Result.success(true) },
            premiumRecords = records,
            connectedFactory = ConnectedPremiumSessionFactory { completed },
            accountConnectionFactory = PremiumAccountConnectionFactory { connection },
            now = { now },
        )

        runtime.foreground()
        advanceUntilIdle()
        runtime.dispatch(PodcastReaderRuntimeEvent.DevelopmentOriginChanged("https://premium.example.test"))
        runtime.dispatch(PodcastReaderRuntimeEvent.ConnectAccount)
        runtime.background()
        advanceUntilIdle()

        assertEquals(1, connection.completeCount)
        assertEquals(0, completed.validateCount)
        assertTrue(runtime.uiState.value.account is AccountUiState.Bootstrapping)

        runtime.foreground()
        advanceUntilIdle()

        assertEquals(1, completed.restoreCount)
        assertTrue(runtime.uiState.value.account is AccountUiState.OnlineUnavailable)
    }

    private fun kotlinx.coroutines.test.TestScope.runtime(
        enginePresence: Result<Boolean> = Result.success(false),
        accountResult: Result<PremiumAccountCredentials?>,
        factory: ConnectedPremiumSessionFactory,
        connectionFactory: PremiumAccountConnectionFactory = rejectingConnectionFactory(),
    ): PodcastReaderRuntime = PodcastReaderRuntime(
        scope = this,
        workDispatcher = StandardTestDispatcher(testScheduler),
        engineRecords = EngineRecordProbe { enginePresence },
        premiumRecords = TestPremiumRecords(accountResult),
        connectedFactory = factory,
        accountConnectionFactory = connectionFactory,
        now = { now },
    )

    private fun account() = PremiumAccountCredentials(
        PremiumOrigin.fromTrustedConfiguration("https://premium.example.test").getOrThrow(),
        PremiumRefreshToken.fromAuthorization("refresh_token_abcdefghijklmnopqrstuvwxyz").getOrThrow(),
    )

    private fun rejectingConnectionFactory() = PremiumAccountConnectionFactory {
        error("account connection was not expected")
    }


    private fun authorizationSession(nextPollAt: Instant) = DeviceAuthorizationSession(
        origin = PremiumOrigin.fromTrustedConfiguration("https://premium.example.test").getOrThrow(),
        deviceCode = DeviceCode.fromAuthorization("device_code_abcdefghijklmnopqrstuvwxyz").getOrThrow(),
        userCode = UserCode.fromAuthorization("K4AA-7BCD").getOrThrow(),
        verificationUri = "https://premium.example.test/device",
        expiresAt = now.plusSeconds(300),
        pollIntervalSeconds = 5,
        nextPollAt = nextPollAt,
    )

    private fun tokens() = AuthorizedPremiumTokens(
        PremiumAccessToken.fromAuthorization("access_token_abcdefghijklmnopqrstuvwxyz").getOrThrow(),
        PremiumRefreshToken.fromAuthorization("refresh_token_abcdefghijklmnopqrstuvwxyz").getOrThrow(),
    )
}

private class TestPremiumRecords(
    private var result: Result<PremiumAccountCredentials?>,
    private val clearResult: Result<Unit> = Result.success(Unit),
) : PremiumAccountRecordAccess {
    var loadCount = 0
    var clearCount = 0

    override fun load(): Result<PremiumAccountCredentials?> = result.also { loadCount += 1 }
    override fun clear(): Result<Unit> = clearResult.also { clearCount += 1 }

    fun install(account: PremiumAccountCredentials) {
        result = Result.success(account)
    }
}

private class CompletedSession(
    private val result: PremiumRestoreResult,
) : ConnectedPremiumSession {
    var restoreCount = 0
    var validateCount = 0

    override suspend fun restore(now: Instant, requestId: String): PremiumRestoreResult = result.also {
        restoreCount += 1
    }

    override suspend fun validateAuthorized(now: Instant, requestId: String): PremiumRestoreResult = result.also {
        validateCount += 1
    }
    override suspend fun signOut(requestId: String) = Unit
}

private class TestAccountConnection(
    private val initial: DeviceAuthorizationTransition,
    private val poll: DeviceAuthorizationTransition,
    private val completedSession: ConnectedPremiumSession,
    private val onComplete: () -> Unit = {},
    private val onBegin: (() -> DeviceAuthorizationTransition)? = null,
) : PremiumAccountConnection {
    var cancelCount = 0
    var completeCount = 0
    var pollCount = 0

    override fun begin(now: Instant, requestId: String): DeviceAuthorizationTransition = onBegin?.invoke() ?: initial

    override fun poll(
        session: DeviceAuthorizationSession,
        now: Instant,
        requestId: String,
    ): DeviceAuthorizationTransition = poll.also { pollCount += 1 }

    override fun cancel(session: DeviceAuthorizationSession) {
        cancelCount += 1
        session.deviceCode.clear()
    }

    override fun complete(tokens: AuthorizedPremiumTokens): Result<ConnectedPremiumSession> {
        completeCount += 1
        onComplete()
        return Result.success(completedSession)
    }
}

private class SequencedPremiumRecords(
    private val account: PremiumAccountCredentials,
    private val clearResults: MutableList<Result<Unit>>,
) : PremiumAccountRecordAccess {
    var clearCount = 0

    override fun load(): Result<PremiumAccountCredentials?> = Result.success(account)

    override fun clear(): Result<Unit> {
        clearCount += 1
        return clearResults.removeAt(0)
    }
}

private class DelayedSignOutSession(
    private val signOutRelease: CompletableDeferred<Unit>,
) : ConnectedPremiumSession {
    override suspend fun restore(now: Instant, requestId: String): PremiumRestoreResult =
        PremiumRestoreResult.Online(
            ProductStateReducer.unavailable(OnlineUnavailableReason.OFFLINE),
        )

    override suspend fun signOut(requestId: String) {
        withContext(NonCancellable) { signOutRelease.await() }
    }

    override suspend fun validateAuthorized(now: Instant, requestId: String): PremiumRestoreResult =
        PremiumRestoreResult.Online(
            ProductStateReducer.unavailable(OnlineUnavailableReason.OFFLINE),
        )
}

private class DelayedSession(
    private val result: CompletableDeferred<PremiumRestoreResult>,
) : ConnectedPremiumSession {
    var restoreCompleted = false
        private set

    override suspend fun restore(now: Instant, requestId: String): PremiumRestoreResult {
        val restored = withContext(NonCancellable) { result.await() }
        restoreCompleted = true
        return restored
    }

    override suspend fun validateAuthorized(now: Instant, requestId: String): PremiumRestoreResult = restore(now, requestId)

    override suspend fun signOut(requestId: String) = Unit
}
