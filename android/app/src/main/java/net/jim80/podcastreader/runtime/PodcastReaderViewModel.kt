package net.jim80.podcastreader.runtime

import android.content.Context
import androidx.lifecycle.ViewModel
import androidx.lifecycle.ViewModelProvider
import androidx.lifecycle.viewModelScope
import java.time.Instant
import kotlinx.coroutines.Dispatchers
import net.jim80.podcastreader.core.ads.HouseAdRequestFactory
import net.jim80.podcastreader.core.ads.HouseAdCtaOpener
import net.jim80.podcastreader.core.ads.HouseAdTransport
import net.jim80.podcastreader.core.engine.EngineCredentialStore
import net.jim80.podcastreader.core.premium.AndroidExternalBrowserLauncher
import net.jim80.podcastreader.core.premium.AuthorizedPremiumTokens
import net.jim80.podcastreader.core.premium.DeviceAuthorizationFlow
import net.jim80.podcastreader.core.premium.DeviceAuthorizationSession
import net.jim80.podcastreader.core.premium.DeviceAuthorizationTransition
import net.jim80.podcastreader.core.premium.PremiumAccountAuthorizer
import net.jim80.podcastreader.core.premium.PremiumAccountCredentials
import net.jim80.podcastreader.core.premium.PremiumCredentialStore
import net.jim80.podcastreader.core.premium.PremiumCurrentUserTransport
import net.jim80.podcastreader.core.premium.PremiumEntitlementTransport
import net.jim80.podcastreader.core.premium.PremiumNativeAuthTransport
import net.jim80.podcastreader.core.premium.PremiumOrigin
import net.jim80.podcastreader.core.premium.PremiumRequestFactory
import net.jim80.podcastreader.core.premium.ProductionPremiumConnectedSession
import net.jim80.podcastreader.core.premium.securePremiumHttpClient
import net.jim80.podcastreader.ui.PodcastReaderActions
import net.jim80.podcastreader.ui.ads.AndroidHouseAdCtaLauncher

internal class PodcastReaderViewModel(
    dependencies: RuntimeDependencies,
) : ViewModel() {
    private val runtime = PodcastReaderRuntime(
        scope = viewModelScope,
        workDispatcher = dependencies.workDispatcher,
        engineRecords = dependencies.engineRecords,
        premiumRecords = dependencies.premiumRecords,
        connectedFactory = dependencies.connectedFactory,
        accountConnectionFactory = dependencies.accountConnectionFactory,
        houseAdCtaOpener = dependencies.houseAdCtaOpener,
        now = dependencies.now,
    )

    val uiState = runtime.uiState
    val actions = PodcastReaderActions(
        onDevelopmentOriginChanged = {
            runtime.dispatch(PodcastReaderRuntimeEvent.DevelopmentOriginChanged(it))
        },
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
    val accountConnectionFactory: PremiumAccountConnectionFactory,
    val houseAdCtaOpener: HouseAdCtaOpener,
    val now: () -> Instant,
)

internal object PodcastReaderProductionComposition {
    fun viewModelFactory(context: Context): ViewModelProvider.Factory {
        val applicationContext = context.applicationContext
        val engineStore = EngineCredentialStore.create(applicationContext)
        val premiumStore = PremiumCredentialStore.create(applicationContext)
        val premiumClient by lazy(LazyThreadSafetyMode.SYNCHRONIZED) {
            securePremiumHttpClient()
        }
        fun accountBrowser() = AndroidExternalBrowserLauncher(applicationContext)
        fun accountAuthorizer() = PremiumAccountAuthorizer(premiumStore)
        fun nativeAuth(requests: PremiumRequestFactory) = PremiumNativeAuthTransport(requests, premiumClient)
        fun connectedSession(
            origin: PremiumOrigin,
            requests: PremiumRequestFactory,
            authorizer: PremiumAccountAuthorizer,
            nativeAuth: PremiumNativeAuthTransport,
        ) = ProductionPremiumConnectedSession(
            authorizer = authorizer,
            nativeAuth = nativeAuth,
            currentUser = PremiumCurrentUserTransport(requests, premiumClient),
            entitlements = PremiumEntitlementTransport(requests, premiumClient),
            houseInventoryFactory = { access ->
                HouseAdTransport(HouseAdRequestFactory(origin), access, premiumClient)
            },
        )
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
                connectedSession(
                    origin = account.origin,
                    requests = requests,
                    authorizer = accountAuthorizer(),
                    nativeAuth = nativeAuth(requests),
                )
            },
            accountConnectionFactory = PremiumAccountConnectionFactory { origin ->
                val requests = PremiumRequestFactory(origin)
                val nativeAuth = nativeAuth(requests)
                val browser = accountBrowser()
                val flow = DeviceAuthorizationFlow(origin, nativeAuth, browser)
                object : PremiumAccountConnection {
                    override fun begin(now: Instant, requestId: String): DeviceAuthorizationTransition =
                        flow.begin(now, requestId)

                    override fun poll(
                        session: DeviceAuthorizationSession,
                        now: Instant,
                        requestId: String,
                    ): DeviceAuthorizationTransition = flow.poll(session, now, requestId)

                    override fun cancel(session: DeviceAuthorizationSession) {
                        flow.cancel(session)
                    }

                    override fun complete(tokens: AuthorizedPremiumTokens) = runCatching {
                        val authorizer = accountAuthorizer()
                        authorizer.completeDeviceAuthorization(origin, tokens).getOrThrow()
                        connectedSession(origin, requests, authorizer, nativeAuth)
                    }
                }
            },
            houseAdCtaOpener = AndroidHouseAdCtaLauncher(applicationContext),
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
