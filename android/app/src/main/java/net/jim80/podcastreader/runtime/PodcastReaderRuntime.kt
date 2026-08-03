package net.jim80.podcastreader.runtime

import java.time.Instant
import kotlinx.coroutines.CoroutineDispatcher
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import net.jim80.podcastreader.core.ads.HouseAdCta
import net.jim80.podcastreader.core.premium.ConnectedPremiumSession
import net.jim80.podcastreader.core.premium.OnlineUnavailableReason
import net.jim80.podcastreader.core.premium.PremiumAccountCredentials
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

internal sealed interface PodcastReaderRuntimeEvent {
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
    private val now: () -> Instant,
) {
    private val lock = Any()
    private var generation = 0L
    private var foreground = false
    private var signingOut = false
    private var operation: Job? = null
    private var connected: ConnectedPremiumSession? = null
    private var snapshot = PodcastReaderRuntimeSnapshot.bootstrapping()
    private val mutableUiState = MutableStateFlow(PodcastReaderUiState.project(snapshot, now()))

    val uiState: StateFlow<PodcastReaderUiState> = mutableUiState.asStateFlow()

    fun foreground() = synchronized(lock) {
        if (foreground) return@synchronized
        foreground = true
        beginRestoreLocked()
    }

    fun background() = synchronized(lock) {
        if (!foreground) return@synchronized
        foreground = false
        invalidateLocked()
        connected = null
    }

    fun dispatch(event: PodcastReaderRuntimeEvent) {
        when (event) {
            // The service remains visibly unconfigured until slice 3 supplies dev-origin entry.
            // Re-probing here is safe and preserves the local zero-construction boundary.
            PodcastReaderRuntimeEvent.ConnectAccount -> retry()
            PodcastReaderRuntimeEvent.CancelAuthorization -> cancelAuthorization()
            PodcastReaderRuntimeEvent.RetryAccount -> retry()
            PodcastReaderRuntimeEvent.SignOut -> signOut()
            is PodcastReaderRuntimeEvent.OpenHouseAd -> rejectUnissuedHouseCta(event.cta)
        }
    }

    private fun retry() = synchronized(lock) {
        if (foreground) beginRestoreLocked()
    }

    private fun cancelAuthorization() = synchronized(lock) {
        if (snapshot.accountPhase != AccountRuntimePhase.AUTHORIZING) return@synchronized
        invalidateLocked()
        connected = null
        publishLocked(PodcastReaderRuntimeSnapshot.local(snapshot.engine))
    }

    private fun signOut() {
        val (session, signOutGeneration) = synchronized(lock) {
            val activeSession = connected
            invalidateLocked()
            signingOut = true
            connected = null
            publishLocked(PodcastReaderRuntimeSnapshot.restoring(snapshot.engine))
            activeSession to generation
        }
        scope.launch(workDispatcher) {
            runCatching { session?.signOut("android-runtime-$signOutGeneration-sign-out") }
            val clearResult = runCatching { premiumRecords.clear().getOrThrow() }
            synchronized(lock) {
                signingOut = false
                if (!isCurrentLocked(signOutGeneration)) return@synchronized
                val next = clearResult.fold(
                    onSuccess = { PodcastReaderRuntimeSnapshot.local(snapshot.engine) },
                    onFailure = {
                        PodcastReaderRuntimeSnapshot.online(
                            ProductStateReducer.unavailable(
                                OnlineUnavailableReason.INCOMPATIBLE_RESPONSE,
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
            publishLocked(PodcastReaderRuntimeSnapshot.local(snapshot.engine))
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
            publishIfCurrent(restoreGeneration, PodcastReaderRuntimeSnapshot.local(engine))
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
                publishIfCurrent(restoreGeneration, PodcastReaderRuntimeSnapshot.local(engine))
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

    internal fun snapshotForTest(): PodcastReaderRuntimeSnapshot = synchronized(lock) { snapshot }
}
