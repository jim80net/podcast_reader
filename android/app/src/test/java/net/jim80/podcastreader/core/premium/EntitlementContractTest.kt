package net.jim80.podcastreader.core.premium

import java.time.Instant
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
