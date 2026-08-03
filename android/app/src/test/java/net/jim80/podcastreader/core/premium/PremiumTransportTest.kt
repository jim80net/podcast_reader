package net.jim80.podcastreader.core.premium

import net.jim80.podcastreader.core.engine.EngineBearer
import net.jim80.podcastreader.core.engine.EngineRequestFactory
import okhttp3.CookieJar
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class PremiumTransportTest {
    private val origin = PremiumOrigin.fromTrustedConfiguration("https://premium.example.ts.net:8443").getOrThrow()
    private val access = PremiumAccessToken.fromAuthorization("premium-access-marker-1234567890").getOrThrow()

    @Test
    fun entitlementRequestUsesOnlyThePremiumOriginAndAccessToken() {
        val request = PremiumRequestFactory(origin).authenticatedGet(PremiumRoute.Entitlements, access)

        assertEquals("https://premium.example.ts.net:8443/v1/me/entitlements", request.url.toString())
        assertEquals("Bearer premium-access-marker-1234567890", request.header("Authorization"))
        assertEquals("application/json", request.header("Accept"))
    }

    @Test
    fun currentUserRequestUsesTheFrozenRouteOnTheSamePremiumOrigin() {
        val request = PremiumRequestFactory(origin).authenticatedGet(PremiumRoute.CurrentUser, access)

        assertEquals("https://premium.example.ts.net:8443/v1/me", request.url.toString())
        assertEquals("Bearer premium-access-marker-1234567890", request.header("Authorization"))
        assertEquals("application/json", request.header("Accept"))
    }

    @Test
    fun premiumTransportHasNoRedirectCookieOrCacheSurface() {
        val client = securePremiumHttpClient()

        assertFalse(client.followRedirects)
        assertFalse(client.followSslRedirects)
        assertEquals(CookieJar.NO_COOKIES, client.cookieJar)
        assertNull(client.cache)
    }

    @Test
    fun requestFactoriesCannotAcceptTheOtherTrustDomainsTokenType() {
        val premiumParameterTypes = PremiumRequestFactory::class.java.methods
            .filter { it.name == "authenticatedGet" }
            .flatMap { it.parameterTypes.asList() }
        val engineParameterTypes = EngineRequestFactory::class.java.methods
            .filter { it.name == "authenticatedGet" }
            .flatMap { it.parameterTypes.asList() }

        assertTrue(premiumParameterTypes.contains(PremiumAccessToken::class.java))
        assertFalse(premiumParameterTypes.contains(EngineBearer::class.java))
        assertTrue(engineParameterTypes.contains(EngineBearer::class.java))
        assertFalse(engineParameterTypes.contains(PremiumAccessToken::class.java))
    }

    @Test
    fun failuresAreBoundedAndNeverEchoCredentials() {
        val failure = PremiumFailureMapper.fromHttp(401, "request-7")
        assertEquals(PremiumFailureCategory.UNAUTHORIZED, failure.category)
        assertFalse(failure.toString().contains(access.value))
    }
}
