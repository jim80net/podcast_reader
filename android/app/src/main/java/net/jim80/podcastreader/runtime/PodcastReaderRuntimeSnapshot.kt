package net.jim80.podcastreader.runtime

import net.jim80.podcastreader.core.ads.HouseInventory
import net.jim80.podcastreader.core.premium.DeviceAuthorizationSession
import net.jim80.podcastreader.core.premium.ProductState
import net.jim80.podcastreader.core.premium.ProductState.ProductStateReducer

internal enum class EngineRuntimeState {
    CHECKING,
    UNPAIRED,
    PAIRED,
    UNAVAILABLE,
}

internal enum class AccountRuntimePhase {
    BOOTSTRAPPING,
    RESTORING,
    LOCAL,
    AUTHORIZING,
    ONLINE,
}

internal class PodcastReaderRuntimeSnapshot private constructor(
    val engine: EngineRuntimeState,
    val accountPhase: AccountRuntimePhase,
    val productState: ProductState?,
    val authorization: DeviceAuthorizationSession?,
    val accountServiceConfigured: Boolean,
    val libraryInventory: HouseInventory?,
    val jobsInventory: HouseInventory?,
) {
    override fun toString(): String = "PodcastReaderRuntimeSnapshot(redacted)"

    companion object {
        fun bootstrapping(engine: EngineRuntimeState = EngineRuntimeState.CHECKING) =
            PodcastReaderRuntimeSnapshot(
                engine = engine,
                accountPhase = AccountRuntimePhase.BOOTSTRAPPING,
                productState = null,
                authorization = null,
                accountServiceConfigured = false,
                libraryInventory = null,
                jobsInventory = null,
            )

        fun restoring(engine: EngineRuntimeState) = PodcastReaderRuntimeSnapshot(
            engine = engine,
            accountPhase = AccountRuntimePhase.RESTORING,
            productState = null,
            authorization = null,
            accountServiceConfigured = false,
            libraryInventory = null,
            jobsInventory = null,
        )

        fun local(
            engine: EngineRuntimeState = EngineRuntimeState.UNPAIRED,
            accountServiceConfigured: Boolean = false,
        ) = PodcastReaderRuntimeSnapshot(
            engine = engine,
            accountPhase = AccountRuntimePhase.LOCAL,
            productState = ProductStateReducer.local(),
            authorization = null,
            accountServiceConfigured = accountServiceConfigured,
            libraryInventory = null,
            jobsInventory = null,
        )

        fun authorizing(
            engine: EngineRuntimeState,
            session: DeviceAuthorizationSession,
        ) = PodcastReaderRuntimeSnapshot(
            engine = engine,
            accountPhase = AccountRuntimePhase.AUTHORIZING,
            productState = ProductStateReducer.local(),
            authorization = session,
            accountServiceConfigured = true,
            libraryInventory = null,
            jobsInventory = null,
        )

        fun online(
            productState: ProductState,
            engine: EngineRuntimeState = EngineRuntimeState.UNPAIRED,
            libraryInventory: HouseInventory? = null,
            jobsInventory: HouseInventory? = null,
        ): PodcastReaderRuntimeSnapshot {
            require(
                productState.fold(
                    onLocal = { false },
                    onOnlineFree = { true },
                    onOnlinePremium = { true },
                    onOnlineUnavailable = { true },
                ),
            ) { "online runtime snapshot requires reducer-issued online truth" }
            return PodcastReaderRuntimeSnapshot(
                engine = engine,
                accountPhase = AccountRuntimePhase.ONLINE,
                productState = productState,
                authorization = null,
                accountServiceConfigured = true,
                libraryInventory = libraryInventory,
                jobsInventory = jobsInventory,
            )
        }
    }
}
