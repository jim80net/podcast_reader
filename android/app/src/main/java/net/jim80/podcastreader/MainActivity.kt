package net.jim80.podcastreader

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import net.jim80.podcastreader.ui.PodcastReaderApp
import net.jim80.podcastreader.ui.theme.PodcastReaderTheme

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent {
            PodcastReaderTheme {
                PodcastReaderApp()
            }
        }
    }
}
