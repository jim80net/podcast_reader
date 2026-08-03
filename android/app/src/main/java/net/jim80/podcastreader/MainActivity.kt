package net.jim80.podcastreader

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import java.time.Instant
import net.jim80.podcastreader.core.premium.ProductStateReducer
import net.jim80.podcastreader.ui.PodcastReaderActions
import net.jim80.podcastreader.ui.PodcastReaderApp
import net.jim80.podcastreader.ui.PodcastReaderUiState
import net.jim80.podcastreader.ui.theme.PodcastReaderTheme

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent {
            PodcastReaderTheme {
                PodcastReaderApp(
                    state = PodcastReaderUiState.project(
                        productState = ProductStateReducer.local(),
                        now = Instant.now(),
                        accountServiceConfigured = false,
                    ),
                    actions = PodcastReaderActions(
                        onConnectAccount = {},
                        onCancelAccountConnect = {},
                        onRetryAccount = {},
                        onSignOut = {},
                        onOpenHouseAd = {},
                    ),
                )
            }
        }
    }
}
