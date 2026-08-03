package net.jim80.podcastreader.core.premium

import java.time.Instant
import kotlinx.serialization.json.decodeFromJsonElement
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import net.jim80.podcastreader.core.premium.ProductState.ProductStateReducer
import net.jim80.podcastreader.support.FixtureProductStates
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class EntitlementContractTest {
    private val fixtureStates = FixtureProductStates(::fixture)

    private fun fixture(name: String): String = requireNotNull(
        javaClass.classLoader?.getResource(name),
    ) { "missing backend-owned fixture $name" }.readText()

    @Test
    fun decodesCommittedFreeFixtureIntoOnlineFree() {
        val dto = premiumJson.decodeFromString<EntitlementV1Dto>(fixture("entitlements-v1-free.json"))
        val state = ProductStateReducer.online(
            dto,
            expectedSubject = "usr_free_fixture",
            now = Instant.parse("2026-08-02T00:01:00Z"),
        )

        assertEquals("online-free", state.kind())
        assertEquals(null, state.freeTruth()?.houseAds)
    }

    @Test
    fun decodesCommittedPremiumFixtureIntoOnlinePremium() {
        val dto = premiumJson.decodeFromString<EntitlementV1Dto>(fixture("entitlements-v1-premium.json"))
        val state = ProductStateReducer.online(
            dto,
            expectedSubject = "usr_premium_fixture",
            now = Instant.parse("2026-08-02T00:01:00Z"),
        )

        assertEquals("online-premium", state.kind())
        assertTrue(requireNotNull(state.premiumTruth()).capabilities.mobileAdFree)
    }

    @Test
    fun passesEverySharedPositiveAndNegativeConformanceVector() {
        val vectors = premiumJson.parseToJsonElement(
            fixture("v1/entitlements/conformance-v1.json"),
        ).jsonObject
        assertEquals(
            setOf("schema_version", "contract", "expected_subject", "now", "valid", "invalid"),
            vectors.keys,
        )
        assertEquals(1, vectors.getValue("schema_version").jsonPrimitive.content.toInt())
        assertEquals("entitlements-v1", vectors.getValue("contract").jsonPrimitive.content)
        val expectedSubject = vectors.getValue("expected_subject").jsonPrimitive.content
        val now = Instant.parse(vectors.getValue("now").jsonPrimitive.content)
        val valid = vectors.getValue("valid").jsonArray
        val invalid = vectors.getValue("invalid").jsonArray
        val names = (valid + invalid).map { it.jsonObject.getValue("name").jsonPrimitive.content }
        assertEquals(names.size, names.toSet().size)
        assertTrue(names.contains("boolean-capability-as-integer"))

        valid.forEach { element ->
            val vector = element.jsonObject
            val name = vector.getValue("name").jsonPrimitive.content
            val dto = premiumJson.decodeFromJsonElement<EntitlementV1Dto>(
                vector.getValue("document"),
            )
            val state = ProductStateReducer.online(dto, expectedSubject, now)
            val actual = state.kind()
            assertEquals(name, vector.getValue("expected_state").jsonPrimitive.content, actual)
        }
        val acceptedInvalidVectors = invalid.mapNotNull { element ->
            val vector = element.jsonObject
            val name = vector.getValue("name").jsonPrimitive.content
            val result = runCatching {
                premiumJson.decodeFromJsonElement<EntitlementV1Dto>(
                    vector.getValue("document"),
                ).validated(expectedSubject).getOrThrow()
            }
            name.takeIf { result.isSuccess }
        }
        assertTrue("invalid vectors accepted: $acceptedInvalidVectors", acceptedInvalidVectors.isEmpty())
    }

    @Test
    fun housePolicyWithinTheFrozenV1ShapeRemainsOnlineFreeAndNotAdFree() {
        val state = fixtureStates.free(houseAds = true)

        assertEquals("online-free", state.kind())
        assertTrue(requireNotNull(state.freeTruth()).houseAds != null)
    }

    @Test
    fun unknownFieldsAndEnumValuesFailClosedAtTheDtoBoundary() {
        val free = fixture("entitlements-v1-free.json")
        val unknownField = free.replaceFirst("{", "{\n  \"client_invented\": true,")
        val unknownTier = free.replace("\"tier\": \"free\"", "\"tier\": \"enterprise\"")

        assertTrue(runCatching { premiumJson.decodeFromString<EntitlementV1Dto>(unknownField) }.isFailure)
        assertTrue(runCatching { premiumJson.decodeFromString<EntitlementV1Dto>(unknownTier) }.isFailure)
    }

    @Test
    fun subjectMismatchStaleTruthAndInconsistentCapabilitiesAreUnavailable() {
        val freeText = fixture("entitlements-v1-free.json")
        val free = premiumJson.decodeFromString<EntitlementV1Dto>(freeText)
        val inconsistent = premiumJson.decodeFromString<EntitlementV1Dto>(
            freeText.replace("\"mobile_ad_free\": false", "\"mobile_ad_free\": true"),
        )

        assertEquals(
            OnlineUnavailableReason.INCOMPATIBLE_RESPONSE,
            ProductStateReducer.online(free, "usr_other", Instant.parse("2026-08-02T00:01:00Z")).reason(),
        )
        assertEquals(
            OnlineUnavailableReason.STALE,
            ProductStateReducer.online(free, "usr_free_fixture", Instant.parse("2026-08-02T00:05:00Z")).reason(),
        )
        assertEquals(
            OnlineUnavailableReason.INCOMPATIBLE_RESPONSE,
            ProductStateReducer.online(
                inconsistent,
                "usr_free_fixture",
                Instant.parse("2026-08-02T00:01:00Z"),
            ).reason(),
        )
    }

    @Test
    fun onlineFailuresNeverChangeTheLocalState() {
        assertEquals("local", ProductStateReducer.local().kind())
        assertEquals(
            OnlineUnavailableReason.OFFLINE,
            ProductStateReducer.unavailable(OnlineUnavailableReason.OFFLINE).reason(),
        )
        assertEquals("local", ProductStateReducer.local().kind())
    }

    private fun ProductState.kind(): String = fold(
        onLocal = { "local" },
        onOnlineFree = { "online-free" },
        onOnlinePremium = { "online-premium" },
        onOnlineUnavailable = { "unavailable" },
    )

    private fun ProductState.freeTruth(): OnlineFreeTruth? = fold(
        onLocal = { null },
        onOnlineFree = { it },
        onOnlinePremium = { null },
        onOnlineUnavailable = { null },
    )

    private fun ProductState.premiumTruth(): OnlinePremiumTruth? = fold(
        onLocal = { null },
        onOnlineFree = { null },
        onOnlinePremium = { it },
        onOnlineUnavailable = { null },
    )

    private fun ProductState.reason(): OnlineUnavailableReason? = fold(
        onLocal = { null },
        onOnlineFree = { null },
        onOnlinePremium = { null },
        onOnlineUnavailable = { it },
    )
}
