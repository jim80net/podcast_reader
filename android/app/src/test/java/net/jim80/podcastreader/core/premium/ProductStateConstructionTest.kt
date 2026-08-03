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
        assertTrue(ProductState::class.java.constructors.all { it.isSynthetic })
        assertTrue(OnlineFreeTruth::class.java.constructors.all { it.isSynthetic })
        assertTrue(OnlinePremiumTruth::class.java.constructors.all { it.isSynthetic })
    }

    @Test
    fun reducerIssuedPayloadsCannotCarryTheOtherOnlineModesCapabilities() {
        val freePayloadMethods = OnlineFreeTruth::class.java.methods.map { it.name }.toSet()
        val premiumPayloadMethods = OnlinePremiumTruth::class.java.methods.map { it.name }.toSet()

        assertTrue("free truth must not expose premium ad-free truth", "getMobileAdFree" !in freePayloadMethods)
        assertTrue("premium truth must not expose house-ad eligibility", "getHouseAds" !in premiumPayloadMethods)
    }
}
