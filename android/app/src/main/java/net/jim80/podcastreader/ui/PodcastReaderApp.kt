package net.jim80.podcastreader.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.semantics.clearAndSetSemantics
import androidx.compose.ui.unit.dp
import net.jim80.podcastreader.core.ads.HouseAdCta
import net.jim80.podcastreader.ui.account.AccountScreen
import net.jim80.podcastreader.ui.ads.JobsHouseAdSlot
import net.jim80.podcastreader.ui.ads.LibraryHouseAdSlot

data class PodcastReaderActions(
    val onDevelopmentOriginChanged: (String) -> Unit,
    val onConnectAccount: () -> Unit,
    val onCancelAccountConnect: () -> Unit,
    val onRetryAccount: () -> Unit,
    val onSignOut: () -> Unit,
    val onOpenHouseAd: (HouseAdCta) -> Unit,
)

@Composable
fun PodcastReaderApp(
    state: PodcastReaderUiState,
    actions: PodcastReaderActions,
    modifier: Modifier = Modifier,
) {
    var destination by remember { mutableStateOf(AppDestination.LIBRARY) }
    Surface(modifier = modifier.fillMaxSize()) {
        Scaffold(
            bottomBar = {
                NavigationBar {
                    AppDestination.entries.forEach { item ->
                        NavigationBarItem(
                            selected = destination == item,
                            onClick = { destination = item },
                            icon = {
                                Text(item.shortLabel.take(1), modifier = Modifier.clearAndSetSemantics {})
                            },
                            label = { Text(item.shortLabel) },
                        )
                    }
                }
            },
        ) { padding ->
            Column(
                modifier = Modifier
                    .padding(padding)
                    .padding(horizontal = 24.dp, vertical = 24.dp)
                    .verticalScroll(rememberScrollState()),
                verticalArrangement = Arrangement.spacedBy(16.dp),
            ) {
                Text("Podcast Reader", style = MaterialTheme.typography.titleLarge)
                when (destination) {
                    AppDestination.CONNECT -> ConnectComputerScreen()
                    AppDestination.LIBRARY -> {
                        LibraryScreen()
                        LibraryHouseAdSlot(state.libraryInventory, actions.onOpenHouseAd)
                    }
                    AppDestination.JOBS -> {
                        JobsScreen()
                        JobsHouseAdSlot(state.jobsInventory, actions.onOpenHouseAd)
                    }
                    AppDestination.ACCOUNT -> AccountScreen(
                        state = state.account,
                        onDevelopmentOriginChanged = actions.onDevelopmentOriginChanged,
                        onConnect = actions.onConnectAccount,
                        onCancelConnect = actions.onCancelAccountConnect,
                        onRetry = actions.onRetryAccount,
                        onSignOut = actions.onSignOut,
                    )
                }
            }
        }
    }
}

@Composable
private fun ConnectComputerScreen() {
    Text("Connect this computer", style = MaterialTheme.typography.headlineMedium)
    Text("Pair this phone with your private home engine to browse and save transcripts.")
    Text(
        "This connection is separate from the optional online Account.",
        color = MaterialTheme.colorScheme.onSurfaceVariant,
    )
}

@Composable
private fun LibraryScreen() {
    Text("Library", style = MaterialTheme.typography.headlineMedium)
    Text("Your private transcript library will appear here after you connect this computer.")
}

@Composable
private fun JobsScreen() {
    Text("Jobs", style = MaterialTheme.typography.headlineMedium)
    Text("Shared and submitted transcript jobs will appear here.")
}
