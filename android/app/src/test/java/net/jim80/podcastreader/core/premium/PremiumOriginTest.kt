package net.jim80.podcastreader.core.premium

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class PremiumOriginTest {
    @Test
    fun acceptsOnlyCanonicalTrustedHttpsOrigins() {
        val expected = "https://premium.example.ts.net:8443"
        val origin = PremiumOrigin.fromTrustedConfiguration(expected).getOrThrow()
        assertEquals(expected, origin.resolve(PremiumRoute.Entitlements).removeSuffix("/v1/me/entitlements"))
    }

    @Test
    fun rejectsCredentialsPathsQueriesFragmentsAndNonCanonicalForms() {
        val rejected = listOf(
            "http://premium.example.ts.net",
            "https://premium.example.ts.net/",
            "https://premium.example.ts.net/device",
            "https://premium.example.ts.net?subject=secret",
            "https://premium.example.ts.net#fragment",
            "https://user@premium.example.ts.net",
            "https://premium.example.ts.net:443",
            "https://PREMIUM.example.ts.net",
            "https://premium.example.ts.net.",
            " https://premium.example.ts.net",
        )

        rejected.forEach { value ->
            assertTrue("accepted $value", PremiumOrigin.fromTrustedConfiguration(value).isFailure)
        }
    }
}
