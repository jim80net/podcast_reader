package net.jim80.podcastreader.runtime

import android.content.Context
import androidx.lifecycle.ViewModel
import androidx.lifecycle.ViewModelProvider
import androidx.lifecycle.viewModelScope
import java.time.Instant
import kotlinx.coroutines.Dispatchers
import net.jim80.podcastreader.core.engine.EngineCredentialStore
import net.jim80.podcastreader.core.premium.PremiumAccountAuthorizer
import net.jim80.podcastreader.core.premium.PremiumAccountCredentials
import net.jim80.podcastreader.core.premium.PremiumCredentialStore
import net.jim80.podcastreader.core.premium.PremiumCurrentUserTransport
import net.jim80.podcastreader.core.premium.PremiumEntitlementTransport
import net.jim80.podcastreader.core.premium.PremiumNativeAuthTransport
import net.jim80.podcastreader.core.premium.PremiumRequestFactory
import net.jim80.podcastreader.core.premium.ProductionPremiumConnectedSession
import net.jim80.podcastreader.core.premium.securePremiumHttpClient
import net.jim80.podcastreader.ui.PodcastReaderActions

internal class PodcastReaderViewModel(
    dependencies: RuntimeDependencies,
) : ViewModel() {
    private val runtime = PodcastReaderRuntime(
        scope = viewModelScope,
        workDispatcher = dependencies.workDispatcher,
        engineRecords = dependencies.engineRecords,
        premiumRecords = dependencies.premiumRecords,
        connectedFactory = dependencies.connectedFactory,
        now = dependencies.now,
    )

    val uiState = runtime.uiState
    val actions = PodcastReaderActions(
        onConnectAccount = { runtime.dispatch(PodcastReaderRuntimeEvent.ConnectAccount) },
        onCancelAccountConnect = { runtime.dispatch(PodcastReaderRuntimeEvent.CancelAuthorization) },
        onRetryAccount = { runtime.dispatch(PodcastReaderRuntimeEvent.RetryAccount) },
        onSignOut = { runtime.dispatch(PodcastReaderRuntimeEvent.SignOut) },
        onOpenHouseAd = { runtime.dispatch(PodcastReaderRuntimeEvent.OpenHouseAd(it)) },
    )

    fun foreground() = runtime.foreground()
    fun background() = runtime.background()
}

internal data class RuntimeDependencies(
    val workDispatcher: kotlinx.coroutines.CoroutineDispatcher,
    val engineRecords: EngineRecordProbe,
    val premiumRecords: PremiumAccountRecordAccess,
    val connectedFactory: ConnectedPremiumSessionFactory,
    val now: () -> Instant,
)

internal object PodcastReaderProductionComposition {
    fun viewModelFactory(context: Context): ViewModelProvider.Factory {
        val applicationContext = context.applicationContext
        val engineStore = EngineCredentialStore.create(applicationContext)
        val premiumStore = PremiumCredentialStore.create(applicationContext)
        val dependencies = RuntimeDependencies(
            workDispatcher = Dispatchers.IO,
            engineRecords = EngineRecordProbe {
                engineStore.load().map { it != null }
            },
            premiumRecords = object : PremiumAccountRecordAccess {
                override fun load(): Result<PremiumAccountCredentials?> = premiumStore.load()
                override fun clear(): Result<Unit> = premiumStore.disconnectLocalRecord()
            },
            connectedFactory = ConnectedPremiumSessionFactory { account ->
                val requests = PremiumRequestFactory(account.origin)
                val client = securePremiumHttpClient()
                ProductionPremiumConnectedSession(
                    authorizer = PremiumAccountAuthorizer(premiumStore),
                    nativeAuth = PremiumNativeAuthTransport(requests, client),
                    currentUser = PremiumCurrentUserTransport(requests, client),
                    entitlements = PremiumEntitlementTransport(requests, client),
                )
            },
            now = Instant::now,
        )
        return object : ViewModelProvider.Factory {
            @Suppress("UNCHECKED_CAST")
            override fun <T : ViewModel> create(modelClass: Class<T>): T {
                require(modelClass.isAssignableFrom(PodcastReaderViewModel::class.java)) {
                    "unsupported ViewModel"
                }
                return PodcastReaderViewModel(dependencies) as T
            }
        }
    }
}
