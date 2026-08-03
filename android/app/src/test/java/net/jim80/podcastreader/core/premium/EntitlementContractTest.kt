package net.jim80.podcastreader.core.premium

import java.time.Instant
import kotlinx.serialization.json.decodeFromJsonElement
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class EntitlementContractTest {
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

        assertTrue(state is ProductState.OnlineFree)
        assertEquals(AdPolicyDto.NONE, (state as ProductState.OnlineFree).entitlement.capabilities.adPolicy)
    }

    @Test
    fun decodesCommittedPremiumFixtureIntoOnlinePremium() {
        val dto = premiumJson.decodeFromString<EntitlementV1Dto>(fixture("entitlements-v1-premium.json"))
        val state = ProductStateReducer.online(
            dto,
            expectedSubject = "usr_premium_fixture",
            now = Instant.parse("2026-08-02T00:01:00Z"),
        )

        assertTrue(state is ProductState.OnlinePremium)
        assertTrue((state as ProductState.OnlinePremium).entitlement.capabilities.mobileAdFree)
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
            val actual = when (state) {
                is ProductState.OnlineFree -> "online-free"
                is ProductState.OnlinePremium -> "online-premium"
                else -> "unavailable"
            }
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
        acceptedInvalidVectors.firstOrNull()?.let { name ->
            when (name) {
                "boolean-capability-as-integer" -> error(name)
                "schema-version-as-boolean" -> error(name)
                "subject-as-number" -> error(name)
                "revision-as-string" -> error(name)
                "revision-above-shared-safe-integer" -> error(name)
                "flags-revision-negative" -> error(name)
                "unknown-root-field" -> error(name)
                "unknown-nested-field" -> error(name)
                "missing-capability" -> error(name)
                "unknown-tier" -> error(name)
                "unknown-source" -> error(name)
                "unsupported-paid-ad-policy" -> error(name)
                "noncanonical-offset-time" -> error(name)
                "fractional-second-time" -> error(name)
                "refresh-not-after-evaluation" -> error(name)
                "free-with-premium-capability" -> error(name)
                "free-with-purchase-source" -> error(name)
                "premium-with-none-source" -> error(name)
                "premium-with-house-ads" -> error(name)
                else -> error("unknown invalid vector accepted")
            }
        }
    }

    @Test
    fun housePolicyWithinTheFrozenV1ShapeRemainsOnlineFreeAndNotAdFree() {
        val house = premiumJson.decodeFromString<EntitlementV1Dto>(
            fixture("entitlements-v1-free.json").replace("\"ad_policy\": \"none\"", "\"ad_policy\": \"house\""),
        )
        val state = ProductStateReducer.online(
            house,
            expectedSubject = "usr_free_fixture",
            now = Instant.parse("2026-08-02T00:01:00Z"),
        )

        assertTrue(state is ProductState.OnlineFree)
        assertEquals(AdPolicyDto.HOUSE, (state as ProductState.OnlineFree).entitlement.capabilities.adPolicy)
        assertTrue(!state.entitlement.capabilities.mobileAdFree)
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
            ProductState.OnlineUnavailable(OnlineUnavailableReason.INCOMPATIBLE_RESPONSE),
            ProductStateReducer.online(free, "usr_other", Instant.parse("2026-08-02T00:01:00Z")),
        )
        assertEquals(
            ProductState.OnlineUnavailable(OnlineUnavailableReason.STALE),
            ProductStateReducer.online(free, "usr_free_fixture", Instant.parse("2026-08-02T00:05:00Z")),
        )
        assertEquals(
            ProductState.OnlineUnavailable(OnlineUnavailableReason.INCOMPATIBLE_RESPONSE),
            ProductStateReducer.online(
                inconsistent,
                "usr_free_fixture",
                Instant.parse("2026-08-02T00:01:00Z"),
            ),
        )
    }

    @Test
    fun onlineFailuresNeverChangeTheLocalState() {
        assertEquals(ProductState.Local, ProductStateReducer.local())
        assertEquals(
            ProductState.OnlineUnavailable(OnlineUnavailableReason.OFFLINE),
            ProductStateReducer.unavailable(OnlineUnavailableReason.OFFLINE),
        )
        assertEquals(ProductState.Local, ProductStateReducer.local())
    }
}
