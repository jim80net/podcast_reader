package net.jim80.podcastreader.ui

import java.time.Instant
import net.jim80.podcastreader.core.ads.HouseAdPlacement
import net.jim80.podcastreader.core.ads.HouseInventory
import net.jim80.podcastreader.core.premium.OnlineUnavailableReason
import net.jim80.podcastreader.core.premium.UserCode
import net.jim80.podcastreader.runtime.AccountConnectionIssue
import net.jim80.podcastreader.runtime.AccountRuntimePhase
import net.jim80.podcastreader.runtime.PodcastReaderRuntimeSnapshot

enum class AppDestination(val label: String, val shortLabel: String) {
    CONNECT("Connect this computer", "Connect"),
    LIBRARY("Library", "Library"),
    JOBS("Jobs", "Jobs"),
    ACCOUNT("Account", "Account"),
}

sealed interface AccountUiState {
    data object Bootstrapping : AccountUiState
    data object Connecting : AccountUiState

    class Local internal constructor(
        val developmentOriginDraft: String,
        val developmentOriginValid: Boolean,
        val connectionIssue: AccountConnectionIssue?,
    ) : AccountUiState {
        override fun toString(): String = "AccountUiState.Local(redacted)"
    }

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
    val libraryInventory: HouseInventory?,
    val jobsInventory: HouseInventory?,
) {
    override fun toString(): String = "PodcastReaderUiState(redacted)"

    companion object {
        internal fun project(
            snapshot: PodcastReaderRuntimeSnapshot,
            now: Instant,
        ): PodcastReaderUiState {
            val productState = snapshot.productState
            val account = when (snapshot.accountPhase) {
                AccountRuntimePhase.BOOTSTRAPPING,
                AccountRuntimePhase.RESTORING -> AccountUiState.Bootstrapping
                AccountRuntimePhase.STARTING_AUTHORIZATION -> AccountUiState.Connecting
                AccountRuntimePhase.LOCAL -> AccountUiState.Local(
                    snapshot.developmentOriginDraft,
                    snapshot.developmentOriginValid,
                    snapshot.connectionIssue,
                )
                AccountRuntimePhase.AUTHORIZING -> snapshot.authorization
                    ?.takeIf { now.isBefore(it.expiresAt) }
                    ?.let { AccountUiState.Authorizing(it.userCode, it.expiresAt) }
                    ?: AccountUiState.Local(
                        developmentOriginDraft = "",
                        developmentOriginValid = false,
                        connectionIssue = AccountConnectionIssue.AUTHORIZATION_EXPIRED,
                    )
                AccountRuntimePhase.ONLINE -> requireNotNull(productState).fold(
                    onLocal = { error("online snapshot contained local truth") },
                    onOnlineFree = { AccountUiState.OnlineFree },
                    onOnlinePremium = { AccountUiState.OnlinePremium },
                    onOnlineUnavailable = { AccountUiState.OnlineUnavailable(it) },
                )
            }
            val adsEligible = productState?.fold(
                onLocal = { false },
                onOnlineFree = { truth -> truth.houseAds?.isActiveAt(now) == true },
                onOnlinePremium = { false },
                onOnlineUnavailable = { false },
            ) == true
            return PodcastReaderUiState(
                account = account,
                libraryInventory = snapshot.libraryInventory.visibleFor(HouseAdPlacement.LIBRARY, now, adsEligible),
                jobsInventory = snapshot.jobsInventory.visibleFor(HouseAdPlacement.JOBS, now, adsEligible),
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
