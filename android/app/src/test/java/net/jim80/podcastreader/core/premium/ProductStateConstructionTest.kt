package net.jim80.podcastreader.core.premium

import java.lang.reflect.Modifier
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class ProductStateConstructionTest {
    @Test
    fun productVariantsAndTruthPayloadsExposeNoPublicConstructors() {
        val variants = ProductState::class.java.declaredClasses.filter {
            ProductState::class.java.isAssignableFrom(it)
        }

        assertEquals(4, variants.size)
        assertTrue(variants.all { Modifier.isPrivate(it.modifiers) })
        ProductState::class.java.assertNoPublicSourceConstructors()
        OnlineFreeTruth::class.java.assertNoPublicSourceConstructors()
        OnlinePremiumTruth::class.java.assertNoPublicSourceConstructors()
    }

    @Test
    fun reducerIssuedPayloadsCannotCarryTheOtherOnlineModesCapabilities() {
        val freePayloadMethods = methodsOf(OnlineFreeTruth::class.java, OnlineFreeCapabilities::class.java)
        val premiumPayloadMethods = methodsOf(OnlinePremiumTruth::class.java, OnlinePremiumCapabilities::class.java)

        assertTrue("free payload must not expose premium ad-free truth", "getMobileAdFree" !in freePayloadMethods)
        assertTrue("premium payload must not expose house-ad eligibility", "getHouseAds" !in premiumPayloadMethods)
    }

    private fun Class<*>.assertNoPublicSourceConstructors() {
        assertTrue("expected a declared constructor on $name", declaredConstructors.isNotEmpty())
        assertTrue(declaredConstructors.filterNot { it.isSynthetic }.all { Modifier.isPrivate(it.modifiers) })
        assertTrue("public source constructor exposed on $name", constructors.none { !it.isSynthetic })
    }

    private fun methodsOf(vararg types: Class<*>): Set<String> =
        types.flatMap { type -> type.methods.map { it.name } }.toSet()
}
