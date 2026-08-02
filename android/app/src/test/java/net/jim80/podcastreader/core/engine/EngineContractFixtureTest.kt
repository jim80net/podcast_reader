package net.jim80.podcastreader.core.engine

import kotlinx.serialization.builtins.ListSerializer
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Test

class EngineContractFixtureTest {
    private fun fixture(name: String): String = requireNotNull(
        javaClass.classLoader?.getResource("contracts/engine/$name"),
    ).readText()

    @Test
    fun decodesHealthFixture() {
        val health = engineJson.decodeFromString<HealthDto>(fixture("health.json"))
        assertEquals("0.1.0", health.version)
        assertEquals("sha256:fixture", health.tokenFingerprint)
        assertFalse(health.toString().contains(health.tokenFingerprint))
    }

    @Test
    fun decodesPairClaimFixtureWithoutRenderingTheBearer() {
        val claim = engineJson.decodeFromString<PairClaimDto>(fixture("pair_claim.json"))
        val bearer = EngineBearer.fromClaim(claim.token).getOrThrow()
        assertFalse(claim.toString().contains(claim.token))
        assertFalse(bearer.toString().contains(claim.token))
    }

    @Test
    fun mapsLibraryFixtureToTheMinimizedAndroidProjection() {
        val entries = engineJson.decodeFromString(
            ListSerializer(LibraryEntryDto.serializer()),
            fixture("library.json"),
        )
        val summary = entries.single().toSummary().getOrThrow()

        assertEquals("a".repeat(64), summary.sourceId)
        assertEquals("Fixture Episode", summary.title)
        assertEquals(1_722_470_400.0, summary.createdAt, 0.0)
        assertFalse(summary.toString().contains(summary.title))
        assertFalse(summary.toString().contains("example.com"))
        assertFalse(summary.toString().contains("/private/engine"))
    }
}
