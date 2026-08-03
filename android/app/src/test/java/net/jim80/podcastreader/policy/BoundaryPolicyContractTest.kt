package net.jim80.podcastreader.policy

import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.boolean
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import net.jim80.podcastreader.core.premium.premiumJson
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class BoundaryPolicyContractTest {
    @Test
    fun executesEveryCommittedBoundaryPolicyVectorInKotlin() {
        val policy = parseFixture("v1/boundary-policy/policy-v1.json").jsonObject
        val vectors = parseFixture("v1/boundary-policy/conformance-v1.json").jsonObject
        exact(vectors, setOf("schema_version", "contract", "valid", "invalid"), "vectors")
        require(vectors.requiredInt("schema_version") == 1) { "unsupported conformance schema" }
        require(vectors.requiredString("contract") == "boundary-policy-v1") {
            "unsupported conformance contract"
        }
        val expectedLegacyIds = legacyIds(policy)
        val valid = vectors.requiredArray("valid")
        val invalid = vectors.requiredArray("invalid")
        val names = (valid + invalid).map { it.jsonObject.requiredString("name") }
        assertEquals(names.sorted(), names)
        assertEquals(names.size, names.toSet().size)

        valid.forEach { vectorElement ->
            val vector = vectorElement.jsonObject
            exact(vector, setOf("name", "document"), "valid vector")
            assertEquals("policy-v1.json", vector.requiredString("document"))
            validatePolicy(policy, expectedLegacyIds)
        }

        val wrongFailures = mutableListOf<String>()
        val accepted = mutableListOf<String>()
        invalid.forEach { vectorElement ->
            val vector = vectorElement.jsonObject
            exact(vector, setOf("name", "mutation", "error_contains"), "invalid vector")
            val name = vector.requiredString("name")
            val expected = vector.requiredString("error_contains")
            val result = runCatching {
                validatePolicy(applyMutation(policy, vector.getValue("mutation")), expectedLegacyIds)
            }
            if (result.isSuccess) {
                accepted += name
            } else if (!requireNotNull(result.exceptionOrNull()).message.orEmpty().contains(expected)) {
                wrongFailures += "$name: ${result.exceptionOrNull()?.message}"
            }
        }
        assertTrue("invalid vectors accepted: $accepted", accepted.isEmpty())
        assertTrue("vectors failed for the wrong reason: $wrongFailures", wrongFailures.isEmpty())
    }

    private fun validatePolicy(policy: JsonObject, expectedLegacyIds: Set<String>) {
        exact(
            policy,
            setOf(
                "schema_version",
                "contract",
                "policy_revision",
                "data_classes",
                "zones",
                "operations",
                "copy_claims",
                "surface_enforcement",
                "exceptions",
            ),
            "$",
        )
        require(policy.requiredInt("schema_version") == 1) { "unsupported policy schema" }
        require(policy.requiredString("contract") == "podcast-reader-boundary-policy") {
            "unexpected contract identifier"
        }
        require(policy.requiredInt("policy_revision") > 0) { "policy revision must be positive" }

        val dataClasses = namedDefinitions(policy.requiredArray("data_classes"), "sensitivity")
        val zones = namedDefinitions(policy.requiredArray("zones"), "retention")
        val operations = validateOperations(policy.requiredArray("operations"), dataClasses, zones)
        validateCopyClaims(policy.requiredArray("copy_claims"), operations)
        validateEnforcement(policy.requiredObject("surface_enforcement"), operations, expectedLegacyIds)
        validateExceptions(policy.requiredArray("exceptions"), operations)

        val governed = listOf("operations", "copy_claims", "exceptions")
            .joinToString("\n") { policy.getValue(it).toString() }
            .lowercase()
        val reserved = setOf(
            "custom-premium-origin",
            "off-device-embedding",
            "production-email",
            "topic-corpus",
            "topic_corpus",
        )
        require(reserved.none(governed::contains)) {
            "unresolved operator fork cannot enter v1 policy"
        }
    }

    private fun namedDefinitions(values: JsonArray, property: String): Set<String> {
        val ids = values.mapIndexed { index, element ->
            val item = element.requiredObject("definition[$index]")
            exact(item, setOf("id", property, "description"), "definition[$index]")
            item.requiredString("id")
        }
        require(ids.isNotEmpty() && ids == ids.sorted() && ids.size == ids.toSet().size) {
            "definition IDs must be unique and sorted"
        }
        return ids.toSet()
    }

    private fun validateOperations(
        values: JsonArray,
        dataClasses: Set<String>,
        zones: Set<String>,
    ): Map<String, JsonObject> {
        val expectedKeys = setOf(
            "id",
            "surface",
            "from_zone",
            "to_zone",
            "transport",
            "method",
            "route",
            "request_contract",
            "response_contract",
            "request_data",
            "response_data",
            "destination_persistence",
            "logging",
            "guards",
            "admitting_issue",
            "enforcement_ids",
        )
        val operations = linkedMapOf<String, JsonObject>()
        values.forEachIndexed { index, element ->
            val path = "operations[$index]"
            val operation = element.requiredObject(path)
            exact(operation, expectedKeys, path)
            val id = operation.requiredString("id")
            require(id !in operations) { "operation IDs must be unique and sorted" }
            val fromZone = operation.requiredString("from_zone")
            val toZone = operation.requiredString("to_zone")
            require(fromZone in zones && toZone in zones) { "unknown zone" }
            val route = operation.requiredString("route")
            require('*' !in route) { "wildcards are forbidden" }
            require(route.startsWith('/')) { "route must be exact" }
            val requestData = operation.requiredStrings("request_data")
            val responseData = operation.requiredStrings("response_data")
            val persistence = operation.requiredStrings("destination_persistence")
            val logging = operation.requiredStrings("logging")
            (requestData + responseData + persistence + logging).forEach { dataClass ->
                require(dataClass in dataClasses) { "unknown data class $dataClass" }
            }
            require(operation.requiredStrings("enforcement_ids").isNotEmpty()) {
                "must name at least one checker"
            }
            validateOperationSemantics(
                id,
                fromZone,
                toZone,
                requestData,
                responseData,
                persistence,
                logging,
                operation.requiredStrings("guards"),
            )
            operations[id] = operation
        }
        require(operations.keys.toList() == operations.keys.sorted()) {
            "operation IDs must be unique and sorted"
        }
        return operations
    }

    private fun validateOperationSemantics(
        id: String,
        fromZone: String,
        toZone: String,
        requestData: List<String>,
        responseData: List<String>,
        persistence: List<String>,
        logging: List<String>,
        guards: List<String>,
    ) {
        val moved = (requestData + responseData).toSet()
        val retained = (persistence + logging).toSet()
        val content = (moved + retained).filterTo(mutableSetOf()) { it.startsWith("content.") }
        require(!(fromZone.startsWith("local-engine.") && toZone.startsWith("premium-service."))) {
            "local engine must not create premium-service operations"
        }
        if (toZone == "premium-service.memory" && "email-delivery" !in id) {
            val forbidden = content + moved.intersect(setOf("locator.feed-url", "locator.media-url"))
            require(forbidden.isEmpty()) { "premium account/ad operation carries local content" }
        }
        if (toZone == "premium-service.database") {
            require(content.isEmpty()) { "premium database cannot retain content" }
        }
        if (fromZone in setOf("desktop.renderer", "transcript-frame") ||
            toZone in setOf("desktop.renderer", "transcript-frame")
        ) {
            require((moved + retained).none { it.startsWith("secret.") }) {
                "renderer boundary cannot carry secrets"
            }
        }
        if (id.endsWith("premium.house-ads")) {
            require(responseData.toSet().subtract(
                setOf("locator.external-cta-url", "metadata.house-creative"),
            ).isEmpty()) { "house creative response is not native-only" }
        }
        if (id in setOf("backend.premium.email-delivery", "desktop.premium.email-delivery")) {
            val required = setOf(
                "explicit-consent",
                "fresh-email-entitlement",
                "no-recipient-field",
                "subject-binding",
            )
            require(guards.toSet().containsAll(required)) {
                "email delivery is missing required guards"
            }
        }
    }

    private fun validateCopyClaims(values: JsonArray, operations: Map<String, JsonObject>) {
        values.forEachIndexed { index, element ->
            val claim = element.requiredObject("copy_claims[$index]")
            exact(
                claim,
                setOf("id", "operation_ids", "surfaces", "canonical_facts", "admitting_issue"),
                "copy_claims[$index]",
            )
            claim.requiredStrings("operation_ids").forEach { operationId ->
                require(operationId in operations) { "unknown operation $operationId" }
            }
        }
    }

    private fun validateEnforcement(
        enforcement: JsonObject,
        operations: Map<String, JsonObject>,
        expectedLegacyIds: Set<String>,
    ) {
        exact(enforcement, setOf("declared_roots", "surfaces", "legacy_fences"), "enforcement")
        val androidRoot = enforcement.requiredArray("declared_roots")
            .map(JsonElement::jsonObject)
            .single { it.requiredString("path") == "android" }
        require(androidRoot.getValue("network_capable").jsonPrimitive.boolean) {
            "undeclared network-capable roots"
        }
        val surfaces = enforcement.requiredObject("surfaces")
        val android = surfaces.requiredObject("android")
        require(android.requiredStrings("checker_ids").isNotEmpty() &&
            android.requiredStrings("consuming_tests").isNotEmpty()
        ) { "must have checkers and consuming tests" }
        val legacy = enforcement.requiredArray("legacy_fences")
        val actualLegacyIds = legacy.map { it.jsonObject.requiredString("id") }.toSet()
        require(actualLegacyIds == expectedLegacyIds) { "complete stage-1 mapping required" }
        legacy.forEach { element ->
            element.jsonObject.requiredStrings("operation_ids").forEach { operationId ->
                require(operationId in operations) { "unknown operation $operationId" }
            }
        }
    }

    private fun validateExceptions(values: JsonArray, operations: Map<String, JsonObject>) {
        values.forEachIndexed { index, element ->
            val exception = element.requiredObject("exceptions[$index]")
            exact(
                exception,
                setOf(
                    "id",
                    "operation_id",
                    "reason",
                    "owner",
                    "expires_on",
                    "removal_condition",
                    "admitting_issue",
                ),
                "exceptions[$index]",
            )
            require(exception.requiredString("operation_id") in operations) { "unknown operation" }
        }
    }

    private fun applyMutation(policy: JsonObject, rawMutation: JsonElement): JsonObject {
        val mutation = rawMutation.jsonObject
        exact(mutation, setOf("op", "path", "value"), "mutation")
        val operation = mutation.requiredString("op")
        require(operation in setOf("add", "remove", "replace")) { "unsupported mutation" }
        val pointer = mutation.requiredString("path")
        require(pointer.startsWith('/')) { "mutation path must be absolute" }
        val tokens = pointer.drop(1).split('/').map {
            it.replace("~1", "/").replace("~0", "~")
        }
        return mutate(policy, tokens, operation, mutation.getValue("value")).jsonObject
    }

    private fun mutate(
        current: JsonElement,
        tokens: List<String>,
        operation: String,
        value: JsonElement,
    ): JsonElement {
        require(tokens.isNotEmpty()) { "mutation path cannot target the document root" }
        val token = tokens.first()
        if (tokens.size == 1) {
            return when (current) {
                is JsonObject -> JsonObject(current.toMutableMap().also { map ->
                    when (operation) {
                        "add" -> require(token !in map) { "add target already exists" }
                        "remove", "replace" -> require(token in map) { "mutation target is absent" }
                    }
                    if (operation == "remove") map.remove(token) else map[token] = value
                })
                is JsonArray -> JsonArray(current.toMutableList().also { list ->
                    val index = if (token == "-") list.size else token.toInt()
                    when (operation) {
                        "add" -> list.add(index, value)
                        "remove" -> list.removeAt(index)
                        else -> list[index] = value
                    }
                })
                else -> error("mutation parent must be an object or array")
            }
        }
        return when (current) {
            is JsonObject -> JsonObject(current.toMutableMap().also { map ->
                map[token] = mutate(requireNotNull(map[token]), tokens.drop(1), operation, value)
            })
            is JsonArray -> JsonArray(current.toMutableList().also { list ->
                val index = token.toInt()
                list[index] = mutate(list[index], tokens.drop(1), operation, value)
            })
            else -> error("mutation path does not resolve")
        }
    }

    private fun legacyIds(policy: JsonObject): Set<String> = policy
        .requiredObject("surface_enforcement")
        .requiredArray("legacy_fences")
        .map { it.jsonObject.requiredString("id") }
        .toSet()

    private fun exact(value: JsonObject, expected: Set<String>, path: String) {
        require(value.keys == expected) { "$path: expected exact keys" }
    }

    private fun JsonElement.requiredObject(path: String): JsonObject = this as? JsonObject
        ?: error("$path must be an object")

    private fun JsonObject.requiredObject(name: String): JsonObject =
        getValue(name).requiredObject(name)

    private fun JsonObject.requiredArray(name: String): JsonArray =
        getValue(name) as? JsonArray ?: error("$name must be an array")

    private fun JsonObject.requiredString(name: String): String {
        val value = getValue(name)
        require(value !is JsonNull) { "$name must be a string" }
        return value.jsonPrimitive.contentOrNull ?: error("$name must be a string")
    }

    private fun JsonObject.requiredInt(name: String): Int =
        requiredString(name).toIntOrNull() ?: error("$name must be an integer")

    private fun JsonObject.requiredStrings(name: String): List<String> =
        requiredArray(name).mapIndexed { index, element ->
            element.jsonPrimitive.contentOrNull ?: error("$name[$index] must be a string")
        }.also { values ->
            require(values.size == values.toSet().size) { "$name contains duplicates" }
        }

    private fun parseFixture(name: String): JsonElement = premiumJson.parseToJsonElement(
        requireNotNull(javaClass.classLoader?.getResource(name)) {
            "missing backend-owned fixture $name"
        }.readText(),
    )
}
