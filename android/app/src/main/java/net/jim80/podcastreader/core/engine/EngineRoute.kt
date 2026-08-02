package net.jim80.podcastreader.core.engine

sealed class EngineRoute protected constructor(internal val path: String) {
    data object PairClaim : EngineRoute("/v1/pair/claim")
    data object Health : EngineRoute("/v1/health")
    data object Library : EngineRoute("/v1/library")

    class Transcript private constructor(sourceId: String) :
        EngineRoute("/v1/transcripts/$sourceId.html") {
        companion object {
            private val sourceIdPattern = Regex("[0-9a-f]{64}")

            fun fromSourceId(sourceId: String): Result<Transcript> = runCatching {
                require(sourceIdPattern.matches(sourceId)) { "invalid source" }
                Transcript(sourceId)
            }
        }
    }
}
