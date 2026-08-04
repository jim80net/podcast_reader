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
    STARTING_AUTHORIZATION,
    AUTHORIZING,
    ONLINE,
}

enum class AccountConnectionIssue {
    INVALID_DEVELOPMENT_ORIGIN,
    CONNECTION_FAILED,
    ACCESS_DENIED,
    AUTHORIZATION_EXPIRED,
}

internal class PodcastReaderRuntimeSnapshot private constructor(
    val engine: EngineRuntimeState,
    val accountPhase: AccountRuntimePhase,
    val productState: ProductState?,
    val authorization: DeviceAuthorizationSession?,
    val developmentOriginDraft: String,
    val developmentOriginValid: Boolean,
    val connectionIssue: AccountConnectionIssue?,
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
                developmentOriginDraft = "",
                developmentOriginValid = false,
                connectionIssue = null,
                libraryInventory = null,
                jobsInventory = null,
            )

        fun restoring(engine: EngineRuntimeState) = PodcastReaderRuntimeSnapshot(
            engine = engine,
            accountPhase = AccountRuntimePhase.RESTORING,
            productState = null,
            authorization = null,
            developmentOriginDraft = "",
            developmentOriginValid = false,
            connectionIssue = null,
            libraryInventory = null,
            jobsInventory = null,
        )

        fun local(
            engine: EngineRuntimeState = EngineRuntimeState.UNPAIRED,
            developmentOriginDraft: String = "",
            developmentOriginValid: Boolean = false,
            connectionIssue: AccountConnectionIssue? = null,
        ) = PodcastReaderRuntimeSnapshot(
            engine = engine,
            accountPhase = AccountRuntimePhase.LOCAL,
            productState = ProductStateReducer.local(),
            authorization = null,
            developmentOriginDraft = developmentOriginDraft,
            developmentOriginValid = developmentOriginValid,
            connectionIssue = connectionIssue,
            libraryInventory = null,
            jobsInventory = null,
        )

        fun startingAuthorization(engine: EngineRuntimeState) = PodcastReaderRuntimeSnapshot(
            engine = engine,
            accountPhase = AccountRuntimePhase.STARTING_AUTHORIZATION,
            productState = null,
            authorization = null,
            developmentOriginDraft = "",
            developmentOriginValid = false,
            connectionIssue = null,
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
            developmentOriginDraft = "",
            developmentOriginValid = false,
            connectionIssue = null,
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
                developmentOriginDraft = "",
                developmentOriginValid = false,
                connectionIssue = null,
                libraryInventory = libraryInventory,
                jobsInventory = jobsInventory,
            )
        }
    }
}
