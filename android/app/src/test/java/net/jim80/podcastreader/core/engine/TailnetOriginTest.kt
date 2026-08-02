package net.jim80.podcastreader.core.engine

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class TailnetOriginTest {
    @Test
    fun canonicalizesApprovedServeForms() {
        val expected = "https://desktop.example-tailnet.ts.net"
        assertEquals(expected, TailnetOrigin.parse(expected).getOrThrow().value)
        assertEquals(expected, TailnetOrigin.parse("$expected/").getOrThrow().value)
        assertEquals(expected, TailnetOrigin.parse("$expected/web/").getOrThrow().value)
    }

    @Test
    fun rejectsAnythingOutsideTheTailnetHttpsBoundary() {
        val rejected = listOf(
            "http://desktop.example-tailnet.ts.net",
            "https://desktop.example-tailnet.ts.net:443",
            "https://desktop.example-tailnet.ts.net/path",
            "https://desktop.example-tailnet.ts.net?code=secret",
            "https://desktop.example-tailnet.ts.net#fragment",
            "https://user@desktop.example-tailnet.ts.net",
            "https://desktop.example-tailnet.ts.net.",
            "https://127.0.0.1",
            "https://example.com",
            " https://desktop.example-tailnet.ts.net",
        )

        rejected.forEach { input ->
            assertTrue("accepted $input", TailnetOrigin.parse(input).isFailure)
        }
    }
}
