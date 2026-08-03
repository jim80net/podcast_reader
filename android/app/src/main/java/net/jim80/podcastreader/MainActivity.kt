package net.jim80.podcastreader

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.viewModels
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import net.jim80.podcastreader.runtime.PodcastReaderProductionComposition
import net.jim80.podcastreader.runtime.PodcastReaderViewModel
import net.jim80.podcastreader.ui.PodcastReaderApp
import net.jim80.podcastreader.ui.theme.PodcastReaderTheme

class MainActivity : ComponentActivity() {
    private val runtimeOwner by viewModels<PodcastReaderViewModel> {
        PodcastReaderProductionComposition.viewModelFactory(applicationContext)
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent {
            val state by runtimeOwner.uiState.collectAsState()
            PodcastReaderTheme {
                PodcastReaderApp(
                    state = state,
                    actions = runtimeOwner.actions,
                )
            }
        }
    }

    override fun onStart() {
        super.onStart()
        runtimeOwner.foreground()
    }

    override fun onStop() {
        if (!isChangingConfigurations) runtimeOwner.background()
        super.onStop()
    }
}
