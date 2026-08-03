package net.jim80.podcastreader.ui.ads

import android.content.Context
import android.content.Intent
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.core.net.toUri
import net.jim80.podcastreader.core.ads.HouseAdCreative
import net.jim80.podcastreader.core.ads.HouseAdCta
import net.jim80.podcastreader.core.ads.HouseAdPlacement
import net.jim80.podcastreader.core.ads.HouseInventory

@Composable
fun LibraryHouseAdSlot(
    inventory: HouseInventory?,
    onOpenCta: (HouseAdCta) -> Unit,
    modifier: Modifier = Modifier,
) = DesignatedHouseAdSlot(HouseAdPlacement.LIBRARY, inventory, onOpenCta, modifier)

@Composable
fun JobsHouseAdSlot(
    inventory: HouseInventory?,
    onOpenCta: (HouseAdCta) -> Unit,
    modifier: Modifier = Modifier,
) = DesignatedHouseAdSlot(HouseAdPlacement.JOBS, inventory, onOpenCta, modifier)

@Composable
private fun DesignatedHouseAdSlot(
    placement: HouseAdPlacement,
    inventory: HouseInventory?,
    onOpenCta: (HouseAdCta) -> Unit,
    modifier: Modifier,
) {
    val creative = inventory?.takeIf { it.placement == placement }?.items?.firstOrNull() ?: return
    HouseAdCard(creative, onOpenCta, modifier)
}

@Composable
private fun HouseAdCard(
    creative: HouseAdCreative,
    onOpenCta: (HouseAdCta) -> Unit,
    modifier: Modifier,
) {
    Card(modifier = modifier.fillMaxWidth()) {
        Column(modifier = Modifier.padding(16.dp)) {
            Text(creative.title, style = MaterialTheme.typography.titleMedium)
            Text(creative.body, style = MaterialTheme.typography.bodyMedium)
            Button(onClick = { onOpenCta(creative.cta) }) {
                Text("Learn more")
            }
        }
    }
}

class AndroidHouseAdCtaLauncher(context: Context) {
    private val applicationContext = context.applicationContext

    fun open(cta: HouseAdCta): Result<Unit> = runCatching {
        applicationContext.startActivity(
            Intent(Intent.ACTION_VIEW, cta.value.toUri())
                .addCategory(Intent.CATEGORY_BROWSABLE)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK),
        )
    }
}
