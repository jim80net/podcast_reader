package net.jim80.podcastreader.core.premium

import kotlinx.serialization.json.decodeFromJsonElement
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class CurrentUserContractTest {
    @Test
    fun committedFixtureDecodesToTheOpaqueSubjectOnlyShape() {
        val dto = premiumJson.decodeFromString<CurrentUserV1Dto>(
            fixture("v1/current-user/current-user-v1.json"),
        )

        assertEquals("usr_current_fixture", dto.validatedSubject().getOrThrow())
        assertEquals("CurrentUserV1Dto(redacted)", dto.toString())
    }

    @Test
    fun executesEveryCommittedCurrentUserConformanceVector() {
        val vectors = premiumJson.parseToJsonElement(
            fixture("v1/current-user/conformance-v1.json"),
        ).jsonObject
        assertEquals(setOf("schema_version", "contract", "valid", "invalid"), vectors.keys)
        assertEquals(1, vectors.getValue("schema_version").jsonPrimitive.content.toInt())
        assertEquals("current-user-v1", vectors.getValue("contract").jsonPrimitive.content)
        val valid = vectors.getValue("valid").jsonArray
        val invalid = vectors.getValue("invalid").jsonArray
        val names = (valid + invalid).map { it.jsonObject.getValue("name").jsonPrimitive.content }
        assertEquals(names.size, names.toSet().size)
        assertTrue(names.contains("opaque-subject-without-format-semantics"))
        assertTrue(names.contains("email-is-not-a-consumer-field"))

        valid.forEach { element ->
            val vector = element.jsonObject
            val name = vector.getValue("name").jsonPrimitive.content
            val result = runCatching {
                premiumJson.decodeFromJsonElement<CurrentUserV1Dto>(vector.getValue("document"))
                    .validatedSubject()
                    .getOrThrow()
            }
            assertTrue("valid vector rejected: $name", result.isSuccess)
        }
        val acceptedInvalid = invalid.mapNotNull { element ->
            val vector = element.jsonObject
            val name = vector.getValue("name").jsonPrimitive.content
            val result = runCatching {
                premiumJson.decodeFromJsonElement<CurrentUserV1Dto>(vector.getValue("document"))
                    .validatedSubject()
                    .getOrThrow()
            }
            name.takeIf { result.isSuccess }
        }
        assertTrue("invalid vectors accepted: $acceptedInvalid", acceptedInvalid.isEmpty())
    }

    private fun fixture(name: String): String = requireNotNull(
        javaClass.classLoader?.getResource(name),
    ) { "missing backend-owned fixture $name" }.readText()
}
