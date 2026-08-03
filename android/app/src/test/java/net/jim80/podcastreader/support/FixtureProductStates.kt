package net.jim80.podcastreader.support

import java.time.Instant
import net.jim80.podcastreader.core.premium.EntitlementV1Dto
import net.jim80.podcastreader.core.premium.OnlineUnavailableReason
import net.jim80.podcastreader.core.premium.ProductState
import net.jim80.podcastreader.core.premium.ProductState.ProductStateReducer
import net.jim80.podcastreader.core.premium.premiumJson

object FixtureProductStates {
    val now: Instant = Instant.parse("2026-08-02T00:01:00Z")

    fun local(): ProductState = ProductStateReducer.local()

    fun unavailable(reason: OnlineUnavailableReason): ProductState = ProductStateReducer.unavailable(reason)

    fun free(houseAds: Boolean = false, at: Instant = now): ProductState = online(
        fixture = "entitlements-v1-free.json",
        expectedSubject = "usr_free_fixture",
        now = at,
        houseAds = houseAds,
    )

    fun premium(at: Instant = now): ProductState = online(
        fixture = "entitlements-v1-premium.json",
        expectedSubject = "usr_premium_fixture",
        now = at,
    )

    private fun online(
        fixture: String,
        expectedSubject: String,
        now: Instant,
        houseAds: Boolean = false,
    ): ProductState {
        val fixtureText = requireNotNull(javaClass.classLoader?.getResource(fixture)) {
            "missing backend-owned fixture $fixture"
        }.readText().let {
            if (houseAds) it.replace("\"ad_policy\": \"none\"", "\"ad_policy\": \"house\"") else it
        }
        return ProductStateReducer.online(
            premiumJson.decodeFromString<EntitlementV1Dto>(fixtureText),
            expectedSubject,
            now,
        )
    }
}
