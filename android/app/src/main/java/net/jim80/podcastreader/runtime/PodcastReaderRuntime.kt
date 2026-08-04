package net.jim80.podcastreader.runtime

import java.time.Duration
import java.time.Instant
import kotlinx.coroutines.CoroutineDispatcher
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import net.jim80.podcastreader.core.ads.HouseAdCta
import net.jim80.podcastreader.core.premium.AuthorizedPremiumTokens
import net.jim80.podcastreader.core.premium.ConnectedPremiumSession
import net.jim80.podcastreader.core.premium.DeviceAuthorizationSession
import net.jim80.podcastreader.core.premium.DeviceAuthorizationTransition
import net.jim80.podcastreader.core.premium.OnlineUnavailableReason
import net.jim80.podcastreader.core.premium.PremiumAccountCredentials
import net.jim80.podcastreader.core.premium.PremiumFailure
import net.jim80.podcastreader.core.premium.PremiumFailureCategory
import net.jim80.podcastreader.core.premium.PremiumOrigin
import net.jim80.podcastreader.core.premium.PremiumRestoreResult
import net.jim80.podcastreader.core.premium.ProductState.ProductStateReducer
import net.jim80.podcastreader.ui.PodcastReaderUiState

internal fun interface EngineRecordProbe {
    fun loadPresence(): Result<Boolean>
}

internal interface PremiumAccountRecordAccess {
    fun load(): Result<PremiumAccountCredentials?>
    fun clear(): Result<Unit>
}

internal fun interface ConnectedPremiumSessionFactory {
    fun create(account: PremiumAccountCredentials): ConnectedPremiumSession
}

internal interface PremiumAccountConnection {
    fun begin(now: Instant, requestId: String): DeviceAuthorizationTransition
    fun poll(
        session: DeviceAuthorizationSession,
        now: Instant,
        requestId: String,
    ): DeviceAuthorizationTransition
    fun cancel(session: DeviceAuthorizationSession)
    fun complete(tokens: AuthorizedPremiumTokens): Result<ConnectedPremiumSession>
}

internal fun interface PremiumAccountConnectionFactory {
    fun create(origin: PremiumOrigin): PremiumAccountConnection
}

internal sealed interface PodcastReaderRuntimeEvent {
    class DevelopmentOriginChanged(val value: String) : PodcastReaderRuntimeEvent {
        override fun toString(): String = "DevelopmentOriginChanged(redacted)"
    }

    data object ConnectAccount : PodcastReaderRuntimeEvent
    data object CancelAuthorization : PodcastReaderRuntimeEvent
    data object RetryAccount : PodcastReaderRuntimeEvent
    data object SignOut : PodcastReaderRuntimeEvent
    data class OpenHouseAd(val cta: HouseAdCta) : PodcastReaderRuntimeEvent
}

internal class PodcastReaderRuntime(
    private val scope: CoroutineScope,
    private val workDispatcher: CoroutineDispatcher,
    private val engineRecords: EngineRecordProbe,
    private val premiumRecords: PremiumAccountRecordAccess,
    private val connectedFactory: ConnectedPremiumSessionFactory,
    private val accountConnectionFactory: PremiumAccountConnectionFactory,
    private val now: () -> Instant,
) {
    private val lock = Any()
    private var generation = 0L
    private var foreground = false
    private var signingOut = false
    private var signOutClearFailed = false
    private var signOutAttempt = 0L
    private var developmentOriginDraft = ""
    private var developmentOrigin: PremiumOrigin? = null
    private var connectionIssue: AccountConnectionIssue? = null
    private var operation: Job? = null
    private var connected: ConnectedPremiumSession? = null
    private var accountConnection: PremiumAccountConnection? = null
    private var authorizationSession: DeviceAuthorizationSession? = null
    private var snapshot = PodcastReaderRuntimeSnapshot.bootstrapping()
    private val mutableUiState = MutableStateFlow(PodcastReaderUiState.project(snapshot, now()))

    val uiState: StateFlow<PodcastReaderUiState> = mutableUiState.asStateFlow()

    fun foreground() = synchronized(lock) {
        if (foreground) return@synchronized
        foreground = true
        if (snapshot.accountPhase.isAuthorizationPhase()) return@synchronized
        beginRestoreLocked()
    }

    fun background() = synchronized(lock) {
        if (!foreground) return@synchronized
        foreground = false
        if (snapshot.accountPhase.isAuthorizationPhase()) return@synchronized
        invalidateLocked()
        connected = null
    }

    fun dispatch(event: PodcastReaderRuntimeEvent) {
        when (event) {
            is PodcastReaderRuntimeEvent.DevelopmentOriginChanged -> updateDevelopmentOrigin(event.value)
            PodcastReaderRuntimeEvent.ConnectAccount -> connectAccount()
            PodcastReaderRuntimeEvent.CancelAuthorization -> cancelAuthorization()
            PodcastReaderRuntimeEvent.RetryAccount -> retry()
            PodcastReaderRuntimeEvent.SignOut -> signOut()
            is PodcastReaderRuntimeEvent.OpenHouseAd -> rejectUnissuedHouseCta(event.cta)
        }
    }

    private fun updateDevelopmentOrigin(value: String) = synchronized(lock) {
        if (snapshot.accountPhase != AccountRuntimePhase.LOCAL) return@synchronized
        developmentOriginDraft = value.take(MAX_DEVELOPMENT_ORIGIN_LENGTH)
        developmentOrigin = PremiumOrigin.fromTrustedConfiguration(developmentOriginDraft).getOrNull()
        connectionIssue = if (developmentOriginDraft.isNotEmpty() && developmentOrigin == null) {
            AccountConnectionIssue.INVALID_DEVELOPMENT_ORIGIN
        } else {
            null
        }
        publishLocalLocked(snapshot.engine)
    }

    private fun connectAccount() = synchronized(lock) {
        if (!foreground || snapshot.accountPhase != AccountRuntimePhase.LOCAL) return@synchronized
        val origin = developmentOrigin ?: run {
            connectionIssue = AccountConnectionIssue.INVALID_DEVELOPMENT_ORIGIN
            publishLocalLocked(snapshot.engine)
            return@synchronized
        }
        invalidateLocked()
        val authorizationGeneration = generation
        connectionIssue = null
        publishLocked(PodcastReaderRuntimeSnapshot.startingAuthorization(snapshot.engine))
        operation = scope.launch(workDispatcher) {
            try {
                runAuthorization(authorizationGeneration, origin)
            } finally {
                abandonAuthorizationIfStillPending(authorizationGeneration)
            }
        }
    }

    private fun abandonAuthorizationIfStillPending(authorizationGeneration: Long) {
        val pending = synchronized(lock) {
            if (!isAuthorizationCurrentLocked(authorizationGeneration)) return
            val value = accountConnection to authorizationSession
            accountConnection = null
            authorizationSession = null
            operation = null
            connectionIssue = AccountConnectionIssue.CONNECTION_FAILED
            publishLocalLocked(snapshot.engine)
            value
        }
        pending.second?.let { pending.first?.cancel(it) }
    }

    private suspend fun runAuthorization(
        authorizationGeneration: Long,
        origin: PremiumOrigin,
    ) {
        val connection = runCatching { accountConnectionFactory.create(origin) }.getOrElse {
            finishAuthorizationLocal(
                authorizationGeneration,
                connection = null,
                session = null,
                issue = AccountConnectionIssue.CONNECTION_FAILED,
            )
            return
        }
        var session: DeviceAuthorizationSession? = null
        var transition = runCatching {
            connection.begin(now(), "android-runtime-$authorizationGeneration-device-start")
        }.getOrElse {
            DeviceAuthorizationTransition.Failed(
                PremiumFailure(
                    PremiumFailureCategory.INCOMPATIBLE_RESPONSE,
                    requestId = "android-runtime-$authorizationGeneration-device-start",
                ),
            )
        }
        while (true) {
            if (!isAuthorizationCurrent(authorizationGeneration)) {
                (transition as? DeviceAuthorizationTransition.Waiting)?.session?.let {
                    runCatching { connection.cancel(it) }
                }
                return
            }
            when (transition) {
                is DeviceAuthorizationTransition.Waiting -> {
                    val waitingSession = transition.session
                    session = waitingSession
                    val accepted = synchronized(lock) {
                        if (!isAuthorizationCurrentLocked(authorizationGeneration)) return@synchronized false
                        accountConnection = connection
                        authorizationSession = waitingSession
                        publishLocked(PodcastReaderRuntimeSnapshot.authorizing(snapshot.engine, waitingSession))
                        true
                    }
                    if (!accepted) {
                        runCatching { connection.cancel(waitingSession) }
                        return
                    }
                    val waitMillis = runCatching {
                        Duration.between(now(), session.nextPollAt).toMillis().coerceAtLeast(0L)
                    }.getOrDefault(0L)
                    delay(waitMillis)
                    if (!isAuthorizationCurrent(authorizationGeneration)) return
                    transition = runCatching {
                        connection.poll(
                            session,
                            now(),
                            "android-runtime-$authorizationGeneration-device-poll",
                        )
                    }.getOrElse {
                        finishAuthorizationLocal(
                            authorizationGeneration,
                            connection,
                            session,
                            AccountConnectionIssue.CONNECTION_FAILED,
                        )
                        return
                    }
                }
                is DeviceAuthorizationTransition.Authorized -> {
                    completeAuthorization(
                        authorizationGeneration,
                        connection,
                        requireNotNull(session),
                        transition.tokens,
                    )
                    return
                }
                DeviceAuthorizationTransition.TooEarly -> {
                    val pending = requireNotNull(session)
                    val waitMillis = runCatching {
                        Duration.between(now(), pending.nextPollAt).toMillis().coerceAtLeast(1L)
                    }.getOrDefault(1L)
                    delay(waitMillis)
                    if (!isAuthorizationCurrent(authorizationGeneration)) return
                    transition = runCatching {
                        connection.poll(
                            pending,
                            now(),
                            "android-runtime-$authorizationGeneration-device-poll",
                        )
                    }.getOrElse {
                        finishAuthorizationLocal(
                            authorizationGeneration,
                            connection,
                            pending,
                            AccountConnectionIssue.CONNECTION_FAILED,
                        )
                        return
                    }
                }
                DeviceAuthorizationTransition.Denied -> {
                    finishAuthorizationLocal(
                        authorizationGeneration,
                        connection,
                        session,
                        AccountConnectionIssue.ACCESS_DENIED,
                    )
                    return
                }
                DeviceAuthorizationTransition.Expired -> {
                    finishAuthorizationLocal(
                        authorizationGeneration,
                        connection,
                        session,
                        AccountConnectionIssue.AUTHORIZATION_EXPIRED,
                    )
                    return
                }
                DeviceAuthorizationTransition.Cancelled -> {
                    finishAuthorizationLocal(
                        authorizationGeneration,
                        connection,
                        session,
                        issue = null,
                    )
                    return
                }
                is DeviceAuthorizationTransition.Failed -> {
                    finishAuthorizationLocal(
                        authorizationGeneration,
                        connection,
                        session,
                        AccountConnectionIssue.CONNECTION_FAILED,
                    )
                    return
                }
            }
        }
    }

    private suspend fun completeAuthorization(
        authorizationGeneration: Long,
        connection: PremiumAccountConnection,
        deviceSession: DeviceAuthorizationSession,
        tokens: AuthorizedPremiumTokens,
    ) {
        val (installed, engine) = synchronized(lock) {
            if (!isAuthorizationCurrentLocked(authorizationGeneration)) return
            connection.cancel(deviceSession)
            authorizationSession = null
            val result = connection.complete(tokens)
            accountConnection = null
            developmentOriginDraft = ""
            developmentOrigin = null
            connectionIssue = null
            result.fold(
                onSuccess = { session ->
                    if (!foreground) {
                        connected = null
                        generation += 1
                        operation = null
                        publishLocked(PodcastReaderRuntimeSnapshot.bootstrapping(snapshot.engine))
                        return
                    }
                    connected = session
                    publishLocked(PodcastReaderRuntimeSnapshot.restoring(snapshot.engine))
                    session to snapshot.engine
                },
                onFailure = {
                    publishLocked(
                        PodcastReaderRuntimeSnapshot.online(
                            ProductStateReducer.unavailable(
                                OnlineUnavailableReason.LOCAL_CREDENTIAL_STORAGE,
                            ),
                            snapshot.engine,
                        ),
                    )
                    return
                },
            )
        }
        val result = withContext(workDispatcher) {
            installed.validateAuthorized(
                now(),
                "android-runtime-$authorizationGeneration-authorized",
            )
        }
        when (result) {
            PremiumRestoreResult.Local -> publishIfCurrent(
                authorizationGeneration,
                PodcastReaderRuntimeSnapshot.online(
                    ProductStateReducer.unavailable(OnlineUnavailableReason.INCOMPATIBLE_RESPONSE),
                    engine,
                ),
            )
            is PremiumRestoreResult.Online -> publishIfCurrent(
                authorizationGeneration,
                PodcastReaderRuntimeSnapshot.online(result.productState, engine),
            )
        }
    }

    private fun finishAuthorizationLocal(
        authorizationGeneration: Long,
        connection: PremiumAccountConnection?,
        session: DeviceAuthorizationSession?,
        issue: AccountConnectionIssue?,
    ) = synchronized(lock) {
        if (!isAuthorizationCurrentLocked(authorizationGeneration)) return@synchronized
        session?.let { connection?.cancel(it) }
        accountConnection = null
        authorizationSession = null
        operation = null
        connectionIssue = issue
        publishLocalLocked(snapshot.engine)
    }

    private fun retry() {
        val retryClear = synchronized(lock) { foreground && signOutClearFailed }
        if (retryClear) {
            signOut()
        } else {
            synchronized(lock) {
                if (foreground) beginRestoreLocked()
            }
        }
    }

    private fun cancelAuthorization() {
        val pending = synchronized(lock) {
            if (!snapshot.accountPhase.isAuthorizationPhase()) return
            val value = accountConnection to authorizationSession
            invalidateLocked()
            accountConnection = null
            authorizationSession = null
            connected = null
            developmentOriginDraft = ""
            developmentOrigin = null
            connectionIssue = null
            publishLocalLocked(snapshot.engine)
            value
        }
        pending.second?.let { pending.first?.cancel(it) }
    }

    private fun signOut() {
        val (session, signOutAttemptId, pendingAuthorization) = synchronized(lock) {
            val activeSession = connected
            val pending = accountConnection to authorizationSession
            invalidateLocked()
            signOutAttempt += 1
            signingOut = true
            signOutClearFailed = false
            developmentOriginDraft = ""
            developmentOrigin = null
            connectionIssue = null
            accountConnection = null
            authorizationSession = null
            connected = null
            publishLocked(PodcastReaderRuntimeSnapshot.restoring(snapshot.engine))
            Triple(activeSession, signOutAttempt, pending)
        }
        pendingAuthorization.second?.let { pendingAuthorization.first?.cancel(it) }
        scope.launch(workDispatcher) {
            runCatching { session?.signOut("android-runtime-$signOutAttemptId-sign-out") }
            val clearResult = runCatching { premiumRecords.clear().getOrThrow() }
            synchronized(lock) {
                if (signOutAttemptId != signOutAttempt) return@synchronized
                signingOut = false
                signOutClearFailed = clearResult.isFailure
                if (!foreground) return@synchronized
                val next = clearResult.fold(
                    onSuccess = { localSnapshot(snapshot.engine) },
                    onFailure = {
                        PodcastReaderRuntimeSnapshot.online(
                            ProductStateReducer.unavailable(
                                OnlineUnavailableReason.LOCAL_CREDENTIAL_STORAGE,
                            ),
                            snapshot.engine,
                        )
                    },
                )
                publishLocked(next)
            }
        }
    }

    private fun rejectUnissuedHouseCta(@Suppress("UNUSED_PARAMETER") cta: HouseAdCta) {
        check(uiState.value.libraryInventory != null || uiState.value.jobsInventory != null) {
            "house-ad CTA was not issued by the runtime"
        }
        error("house-ad CTA wiring belongs to slice 4")
    }

    private fun beginRestoreLocked() {
        if (signingOut) {
            publishLocked(PodcastReaderRuntimeSnapshot.restoring(snapshot.engine))
            return
        }
        if (signOutClearFailed) {
            publishLocked(
                PodcastReaderRuntimeSnapshot.online(
                    ProductStateReducer.unavailable(
                        OnlineUnavailableReason.LOCAL_CREDENTIAL_STORAGE,
                    ),
                    snapshot.engine,
                ),
            )
            return
        }
        invalidateLocked()
        val restoreGeneration = generation
        publishLocked(PodcastReaderRuntimeSnapshot.bootstrapping(snapshot.engine))
        operation = scope.launch(workDispatcher) {
            val engine = engineRecords.loadPresence().fold(
                onSuccess = { if (it) EngineRuntimeState.PAIRED else EngineRuntimeState.UNPAIRED },
                onFailure = { EngineRuntimeState.UNAVAILABLE },
            )
            val account = premiumRecords.load()
            if (!isCurrent(restoreGeneration)) return@launch
            account.fold(
                onSuccess = { credentials -> restoreAccount(restoreGeneration, engine, credentials) },
                onFailure = {
                    publishIfCurrent(
                        restoreGeneration,
                        PodcastReaderRuntimeSnapshot.online(
                            ProductStateReducer.unavailable(OnlineUnavailableReason.INCOMPATIBLE_RESPONSE),
                            engine,
                        ),
                    )
                },
            )
        }
    }

    private suspend fun restoreAccount(
        restoreGeneration: Long,
        engine: EngineRuntimeState,
        account: PremiumAccountCredentials?,
    ) {
        if (account == null) {
            publishIfCurrent(restoreGeneration, localSnapshot(engine))
            return
        }
        publishIfCurrent(restoreGeneration, PodcastReaderRuntimeSnapshot.restoring(engine))
        if (!isCurrent(restoreGeneration)) return
        val session = runCatching { connectedFactory.create(account) }.getOrElse {
            publishIfCurrent(
                restoreGeneration,
                PodcastReaderRuntimeSnapshot.online(
                    ProductStateReducer.unavailable(OnlineUnavailableReason.INCOMPATIBLE_RESPONSE),
                    engine,
                ),
            )
            return
        }
        synchronized(lock) {
            if (!isCurrentLocked(restoreGeneration)) return
            connected = session
        }
        val result = withContext(workDispatcher) {
            session.restore(now(), "android-runtime-$restoreGeneration-restore")
        }
        when (result) {
            PremiumRestoreResult.Local -> {
                synchronized(lock) {
                    if (isCurrentLocked(restoreGeneration)) connected = null
                }
                publishIfCurrent(restoreGeneration, localSnapshot(engine))
            }
            is PremiumRestoreResult.Online -> publishIfCurrent(
                restoreGeneration,
                PodcastReaderRuntimeSnapshot.online(result.productState, engine),
            )
        }
    }

    private fun invalidateLocked() {
        generation += 1
        operation?.cancel()
        operation = null
    }

    private fun publishIfCurrent(expectedGeneration: Long, value: PodcastReaderRuntimeSnapshot) =
        synchronized(lock) {
            if (isCurrentLocked(expectedGeneration)) publishLocked(value)
        }

    private fun publishLocked(value: PodcastReaderRuntimeSnapshot) {
        snapshot = value
        mutableUiState.value = PodcastReaderUiState.project(value, now())
    }

    private fun isCurrent(expectedGeneration: Long): Boolean = synchronized(lock) {
        isCurrentLocked(expectedGeneration)
    }

    private fun isCurrentLocked(expectedGeneration: Long): Boolean =
        foreground && generation == expectedGeneration

    private fun isAuthorizationCurrent(expectedGeneration: Long): Boolean = synchronized(lock) {
        isAuthorizationCurrentLocked(expectedGeneration)
    }

    private fun isAuthorizationCurrentLocked(expectedGeneration: Long): Boolean =
        generation == expectedGeneration && snapshot.accountPhase.isAuthorizationPhase()

    private fun localSnapshot(engine: EngineRuntimeState): PodcastReaderRuntimeSnapshot =
        PodcastReaderRuntimeSnapshot.local(
            engine = engine,
            developmentOriginDraft = developmentOriginDraft,
            developmentOriginValid = developmentOrigin != null,
            connectionIssue = connectionIssue,
        )

    private fun publishLocalLocked(engine: EngineRuntimeState) = publishLocked(localSnapshot(engine))

    internal fun snapshotForTest(): PodcastReaderRuntimeSnapshot = synchronized(lock) { snapshot }
}

private fun AccountRuntimePhase.isAuthorizationPhase(): Boolean =
    this == AccountRuntimePhase.STARTING_AUTHORIZATION || this == AccountRuntimePhase.AUTHORIZING

private const val MAX_DEVELOPMENT_ORIGIN_LENGTH = 2048
