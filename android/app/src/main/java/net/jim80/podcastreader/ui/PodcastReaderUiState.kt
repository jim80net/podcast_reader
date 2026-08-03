package net.jim80.podcastreader.ui

import java.time.Instant
import net.jim80.podcastreader.core.ads.HouseAdPlacement
import net.jim80.podcastreader.core.ads.HouseInventory
import net.jim80.podcastreader.core.premium.DeviceAuthorizationSession
import net.jim80.podcastreader.core.premium.OnlineUnavailableReason
import net.jim80.podcastreader.core.premium.ProductState
import net.jim80.podcastreader.core.premium.UserCode

enum class AppDestination(val label: String, val shortLabel: String) {
    CONNECT("Connect this computer", "Connect"),
    LIBRARY("Library", "Library"),
    JOBS("Jobs", "Jobs"),
    ACCOUNT("Account", "Account"),
}

sealed interface AccountUiState {
    data object Local : AccountUiState

    class Authorizing internal constructor(
        val userCode: UserCode,
        val expiresAt: Instant,
    ) : AccountUiState {
        override fun toString(): String = "AccountUiState.Authorizing(redacted)"
    }

    data object OnlineFree : AccountUiState
    data object OnlinePremium : AccountUiState
    data class OnlineUnavailable(val reason: OnlineUnavailableReason) : AccountUiState
}

class PodcastReaderUiState private constructor(
    val account: AccountUiState,
    val accountServiceConfigured: Boolean,
    val libraryInventory: HouseInventory?,
    val jobsInventory: HouseInventory?,
) {
    override fun toString(): String = "PodcastReaderUiState(redacted)"

    companion object {
        fun project(
            productState: ProductState,
            now: Instant,
            accountServiceConfigured: Boolean,
            authorization: DeviceAuthorizationSession? = null,
            libraryInventory: HouseInventory? = null,
            jobsInventory: HouseInventory? = null,
        ): PodcastReaderUiState {
            val activeAuthorization = authorization?.takeIf { now.isBefore(it.expiresAt) }
            val account = activeAuthorization?.let {
                AccountUiState.Authorizing(it.userCode, it.expiresAt)
            } ?: productState.fold(
                onLocal = { AccountUiState.Local },
                onOnlineFree = { AccountUiState.OnlineFree },
                onOnlinePremium = { AccountUiState.OnlinePremium },
                onOnlineUnavailable = { AccountUiState.OnlineUnavailable(it) },
            )
            val adsEligible = productState.fold(
                onLocal = { false },
                onOnlineFree = { truth -> truth.houseAds?.isActiveAt(now) == true },
                onOnlinePremium = { false },
                onOnlineUnavailable = { false },
            )
            return PodcastReaderUiState(
                account = account,
                accountServiceConfigured = accountServiceConfigured,
                libraryInventory = libraryInventory.visibleFor(HouseAdPlacement.LIBRARY, now, adsEligible),
                jobsInventory = jobsInventory.visibleFor(HouseAdPlacement.JOBS, now, adsEligible),
            )
        }
    }
}

private fun HouseInventory?.visibleFor(
    placement: HouseAdPlacement,
    now: Instant,
    eligible: Boolean,
): HouseInventory? = this?.takeIf {
    eligible && it.placement == placement && now.isBefore(it.expiresAt)
}
