package net.jim80.podcastreader.core.ads

import java.time.Instant
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class HouseAdContractTest {
    private val now = Instant.parse("2026-08-03T00:00:00Z")
    private val validUntil = Instant.parse("2026-08-03T00:05:00Z")

    @Test
    fun eligibleLibraryFixtureMapsToBoundedNativeText() {
        val dto = decode("eligible-library.json")
        val inventory = dto.validated(HouseAdPlacement.LIBRARY, now, validUntil).getOrThrow()

        assertEquals(HouseAdPlacement.LIBRARY, inventory.placement)
        assertEquals("Read without losing your place", inventory.items.single().title)
        assertEquals("https://example.com/podcast-reader", inventory.items.single().cta.value)
        assertFalse(inventory.toString().contains(inventory.items.single().body))
    }

    @Test
    fun hostileMarkupRemainsLiteralTextAndNeverBecomesExecutableContent() {
        val dto = decode("hostile-text.json")
        val creative = dto.validated(HouseAdPlacement.LIBRARY, now, validUntil).getOrThrow().items.single()

        assertEquals("<script>alert('title')</script>", creative.title)
        assertEquals("<img src=x onerror=alert('body')> & must remain inert text.", creative.body)
        assertFalse(creative::class.java.declaredMethods.any { it.name.contains("html", ignoreCase = true) })
    }

    @Test
    fun malformedCreativeAndTrackingCtasFailClosed() {
        assertTrue(decode("malformed.json").validated(HouseAdPlacement.LIBRARY, now, validUntil).isFailure)
        listOf(
            "http://example.com",
            "https://user@example.com/path",
            "https://example.com/path?account=123",
            "https://example.com/path#pixel",
            "https://example.com/space here",
        ).forEach { assertTrue(HouseAdCta.fromContract(it).isFailure) }
    }

    @Test
    fun forwardAdditiveMembersAreIgnoredWithoutWideningConsumedValues() {
        val dto = decode("forward-additive.json")
        assertTrue(dto.validated(HouseAdPlacement.JOBS, now, validUntil).isSuccess)
        assertTrue(dto.validated(HouseAdPlacement.LIBRARY, now, validUntil).isFailure)
    }

    @Test
    fun backendReaderInventoryHasNoRepresentableAndroidPlacement() {
        val reader = decode("eligible-reader.json")

        assertFalse(HouseAdPlacement.entries.any { it.backendSlot == "reader" })
        HouseAdPlacement.entries.forEach { assertTrue(reader.validated(it, now, validUntil).isFailure) }
    }

    @Test
    fun inventoryCannotOutliveTheFiveMinuteContractWindow() {
        val dto = decode("eligible-library.json").copy(expiresAt = "2026-08-03T00:05:01Z")

        assertTrue(dto.validated(HouseAdPlacement.LIBRARY, now, validUntil.plusSeconds(1)).isFailure)
    }

    @Test
    fun noContentFixtureFreezesEmpty204Semantics() {
        val fixture = houseAdJson.decodeFromString<NoContentFixtureV1>(fixture("no-content.json"))
        assertEquals(1, fixture.schemaVersion)
        assertEquals(204, fixture.status)
        assertNull(fixture.body)
    }

    private fun decode(name: String) = houseAdJson.decodeFromString<HouseInventoryV1Dto>(fixture(name))

    private fun fixture(name: String): String = requireNotNull(
        javaClass.classLoader?.getResource("v1/ads/$name"),
    ) { "missing backend-owned ad fixture $name" }.readText()
}
