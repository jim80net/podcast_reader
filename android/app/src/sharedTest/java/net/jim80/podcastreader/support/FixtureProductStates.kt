package net.jim80.podcastreader.support

import java.time.Instant
import kotlinx.serialization.json.decodeFromJsonElement
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import net.jim80.podcastreader.core.premium.EntitlementV1Dto
import net.jim80.podcastreader.core.premium.OnlineUnavailableReason
import net.jim80.podcastreader.core.premium.ProductState
import net.jim80.podcastreader.core.premium.ProductState.ProductStateReducer
import net.jim80.podcastreader.core.premium.premiumJson

class FixtureProductStates(
    private val readContract: (String) -> String,
) {
    private val conformance by lazy {
        premiumJson.parseToJsonElement(readContract(CONFORMANCE_FIXTURE)).jsonObject
    }

    val now: Instant by lazy {
        Instant.parse(conformance.getValue("now").jsonPrimitive.content)
    }

    fun local(): ProductState = ProductStateReducer.local()

    fun unavailable(reason: OnlineUnavailableReason): ProductState = ProductStateReducer.unavailable(reason)

    fun free(houseAds: Boolean = false, at: Instant = now): ProductState = if (houseAds) {
        online(
            dto = conformanceDocument("free-admin-house"),
            expectedSubject = conformance.getValue("expected_subject").jsonPrimitive.content,
            now = at,
        )
    } else {
        online(
            dto = premiumJson.decodeFromString<EntitlementV1Dto>(readContract("entitlements-v1-free.json")),
            expectedSubject = "usr_free_fixture",
            now = at,
        )
    }

    fun premium(at: Instant = now): ProductState = online(
        dto = premiumJson.decodeFromString<EntitlementV1Dto>(readContract("entitlements-v1-premium.json")),
        expectedSubject = "usr_premium_fixture",
        now = at,
    )

    private fun conformanceDocument(name: String): EntitlementV1Dto {
        val vector = conformance.getValue("valid").jsonArray.single {
            it.jsonObject.getValue("name").jsonPrimitive.content == name
        }.jsonObject
        return premiumJson.decodeFromJsonElement(vector.getValue("document"))
    }

    private fun online(
        dto: EntitlementV1Dto,
        expectedSubject: String,
        now: Instant,
    ): ProductState = ProductStateReducer.online(dto, expectedSubject, now)

    private companion object {
        const val CONFORMANCE_FIXTURE = "v1/entitlements/conformance-v1.json"
    }
}
