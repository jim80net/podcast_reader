package net.jim80.podcastreader.acceptance

import android.content.Context
import android.content.ContextWrapper
import android.content.Intent
import androidx.test.core.app.ApplicationProvider
import androidx.test.ext.junit.runners.AndroidJUnit4
import net.jim80.podcastreader.core.premium.AndroidExternalBrowserLauncher
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith

@RunWith(AndroidJUnit4::class)
class DeviceAuthorizationIntentInstrumentedTest {
    @Test
    fun verificationUsesAnExactCredentialFreeSystemBrowserIntent() {
        val context = CapturingContext(ApplicationProvider.getApplicationContext())
        val uri = "https://premium.example.com/device"

        AndroidExternalBrowserLauncher(context).open(uri).getOrThrow()

        assertNotNull(context.startedIntent)
        val intent = requireNotNull(context.startedIntent)
        assertEquals(Intent.ACTION_VIEW, intent.action)
        assertEquals(uri, intent.dataString)
        assertTrue(intent.categories.orEmpty().contains(Intent.CATEGORY_BROWSABLE))
        assertTrue(intent.flags.and(Intent.FLAG_ACTIVITY_NEW_TASK) != 0)
        assertNull(intent.clipData)
        assertFalse(intent.hasExtra(AcceptanceSecrets.DEVICE_CODE))
        assertTrue(intent.extras == null || intent.extras!!.isEmpty)
        AcceptanceSecrets.fullAndPrefixMarkers.forEach { marker ->
            assertFalse(intent.toUri(Intent.URI_INTENT_SCHEME).contains(marker))
        }
    }

    private class CapturingContext(base: Context) : ContextWrapper(base) {
        var startedIntent: Intent? = null

        override fun getApplicationContext(): Context = this

        override fun startActivity(intent: Intent) {
            startedIntent = intent
        }
    }
}
