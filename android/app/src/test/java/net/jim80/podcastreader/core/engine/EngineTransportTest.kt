package net.jim80.podcastreader.core.engine

import java.io.IOException
import okhttp3.CookieJar
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class EngineTransportTest {
    private val origin = TailnetOrigin.parse("https://desktop.example-tailnet.ts.net").getOrThrow()
    private val bearer = EngineBearer.fromClaim("engine-secret-marker").getOrThrow()

    @Test
    fun authenticatedRequestsCanOnlyResolveConstantRoutesOnTheSavedOrigin() {
        val request = EngineRequestFactory(origin).authenticatedGet(EngineRoute.Library, bearer)

        assertEquals("https://desktop.example-tailnet.ts.net/v1/library", request.url.toString())
        assertEquals("Bearer engine-secret-marker", request.header("Authorization"))
    }

    @Test
    fun transportDisablesRedirectsCookiesAndDiskCache() {
        val client = secureEngineHttpClient()

        assertFalse(client.followRedirects)
        assertFalse(client.followSslRedirects)
        assertNull(client.cache)
        assertEquals(CookieJar.NO_COOKIES, client.cookieJar)
    }

    @Test
    fun redactingErrorMapperNeverEchoesLibraryResponseOrCredential() {
        val secret = "engine-secret-marker https://source.example/private"
        val failure = EngineFailureMapper.fromIo(IOException(secret), requestId = "android-request-1")

        val rendered = failure.toString()
        assertEquals(EngineFailureCategory.NETWORK, failure.category)
        assertTrue(rendered.contains("android-request-1"))
        assertFalse(rendered.contains(secret))
        assertFalse(rendered.contains("engine-secret-marker"))
    }

    @Test
    fun redirectsAreUnsafeEndpointFailures() {
        val failure = EngineFailureMapper.fromHttp(302, requestId = "android-request-2")

        assertEquals(EngineFailureCategory.UNSAFE_ENDPOINT, failure.category)
        assertEquals(302, failure.httpStatus)
    }
}
