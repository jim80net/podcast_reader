package net.jim80.podcastreader.ui.theme

import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color

private val LightColors = lightColorScheme(
    primary = Color(0xFF9A3B2E),
    background = Color(0xFFFFF9F3),
    surface = Color(0xFFFFF9F3),
)

private val DarkColors = darkColorScheme(
    primary = Color(0xFFE0876F),
    background = Color(0xFF1C1917),
    surface = Color(0xFF1C1917),
)

@Composable
fun PodcastReaderTheme(
    darkTheme: Boolean = isSystemInDarkTheme(),
    content: @Composable () -> Unit,
) {
    MaterialTheme(
        colorScheme = if (darkTheme) DarkColors else LightColors,
        content = content,
    )
}
