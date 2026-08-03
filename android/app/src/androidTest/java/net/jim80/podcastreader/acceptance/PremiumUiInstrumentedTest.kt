package net.jim80.podcastreader.acceptance

import android.view.WindowManager
import androidx.activity.ComponentActivity
import androidx.compose.runtime.mutableStateOf
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.test.platform.app.InstrumentationRegistry
import net.jim80.podcastreader.core.ads.HouseAdCreative
import net.jim80.podcastreader.core.ads.HouseAdCta
import net.jim80.podcastreader.core.ads.HouseAdPlacement
import net.jim80.podcastreader.core.ads.HouseInventory
import net.jim80.podcastreader.core.premium.OnlineUnavailableReason
import net.jim80.podcastreader.core.premium.UserCode
import net.jim80.podcastreader.support.FixtureProductStates
import net.jim80.podcastreader.ui.AccountUiState
import net.jim80.podcastreader.ui.PodcastReaderActions
import net.jim80.podcastreader.ui.PodcastReaderApp
import net.jim80.podcastreader.ui.PodcastReaderUiState
import net.jim80.podcastreader.ui.account.AccountScreen
import net.jim80.podcastreader.ui.theme.PodcastReaderTheme
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test

class PremiumUiInstrumentedTest {
    @get:Rule
    val compose = createAndroidComposeRule<ComponentActivity>()

    private val fixtures = FixtureProductStates(::fixture)
    private val now by lazy { fixtures.now }

    @Test
    fun authorizingCodeIsVisibleOnlyWhileTheWindowIsSecure() {
        val state = mutableStateOf<AccountUiState>(
            AccountUiState.Authorizing(
                UserCode.fromAuthorization("K4AA-7BCD").getOrThrow(),
                now.plusSeconds(300),
            ),
        )
        compose.setContent {
            PodcastReaderTheme {
                AccountScreen(state.value, true, {}, {}, {}, {})
            }
        }

        compose.onNodeWithText("K4AA-7BCD").assertIsDisplayed()
        compose.runOnIdle { assertTrue(compose.activity.isSecure()) }

        compose.runOnIdle { state.value = AccountUiState.Local }
        compose.waitForIdle()
        compose.onNodeWithText("K4AA-7BCD").assertDoesNotExist()
        compose.runOnIdle { assertFalse(compose.activity.isSecure()) }
    }

    @Test
    fun freshOnlineFreeMountsOnlyItsPlacementMatchedNativeText() {
        val state = PodcastReaderUiState.project(
            productState = fixtures.free(houseAds = true),
            now = now,
            accountServiceConfigured = true,
            libraryInventory = inventory(HouseAdPlacement.LIBRARY, "Library acceptance message"),
            jobsInventory = inventory(HouseAdPlacement.JOBS, "Jobs acceptance message"),
        )
        compose.setContent { PodcastReaderTheme { PodcastReaderApp(state, noOpActions) } }

        compose.onNodeWithText("Library acceptance message").assertIsDisplayed()
        compose.onNodeWithText("Jobs acceptance message").assertDoesNotExist()
        compose.onNodeWithText("Jobs").performClick()
        compose.onNodeWithText("Library acceptance message").assertDoesNotExist()
        compose.onNodeWithText("Jobs acceptance message").assertIsDisplayed()
        compose.onNodeWithText("Account").performClick()
        compose.onNodeWithText("Jobs acceptance message").assertDoesNotExist()
        compose.onNodeWithText("Online free").assertIsDisplayed()
    }

    @Test
    fun unavailableAndPremiumStatesNeverMountRemoteInventory() {
        val inventory = inventory(HouseAdPlacement.LIBRARY, "Must stay absent")
        listOf(
            fixtures.unavailable(OnlineUnavailableReason.OFFLINE),
            fixtures.premium(),
        ).forEach { productState ->
            val state = PodcastReaderUiState.project(productState, now, true, libraryInventory = inventory)
            assertTrue(state.libraryInventory == null)
            assertTrue(state.jobsInventory == null)
        }
        compose.setContent {
            PodcastReaderTheme {
                PodcastReaderApp(
                    PodcastReaderUiState.project(
                        fixtures.unavailable(OnlineUnavailableReason.OFFLINE),
                        now,
                        true,
                        libraryInventory = inventory,
                    ),
                    noOpActions,
                )
            }
        }
        compose.onNodeWithText("Must stay absent").assertDoesNotExist()
        compose.onNodeWithText("Account").performClick()
        compose.onNodeWithText("Online features unavailable").assertIsDisplayed()
        compose.onNodeWithText("The premium service is offline. No ads are fetched or shown.").assertIsDisplayed()
    }

    private fun ComponentActivity.isSecure(): Boolean =
        window.attributes.flags.and(WindowManager.LayoutParams.FLAG_SECURE) != 0

    private fun fixture(name: String): String =
        InstrumentationRegistry.getInstrumentation().context.assets.open(name)
            .bufferedReader()
            .use { it.readText() }

    private fun inventory(placement: HouseAdPlacement, title: String) = HouseInventory(
        placement = placement,
        inventoryRevision = 1,
        expiresAt = now.plusSeconds(60),
        items = listOf(
            HouseAdCreative(
                id = "ad_acceptance",
                revision = 1,
                title = title,
                body = "Native plain text only",
                cta = HouseAdCta.fromContract("https://example.com/acceptance").getOrThrow(),
            ),
        ),
    )

    private companion object {
        val noOpActions = PodcastReaderActions({}, {}, {}, {}, {})
    }
}
