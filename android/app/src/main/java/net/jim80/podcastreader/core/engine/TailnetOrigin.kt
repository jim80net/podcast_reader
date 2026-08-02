package net.jim80.podcastreader.core.engine

import java.net.IDN
import java.net.URI

@JvmInline
value class TailnetOrigin private constructor(val value: String) {
    companion object {
        fun parse(input: String): Result<TailnetOrigin> = runCatching {
            require(input == input.trim()) { "invalid endpoint" }
            require(input.none { it.isISOControl() }) { "invalid endpoint" }

            val uri = URI(input)
            require(uri.scheme == "https") { "invalid endpoint" }
            require(uri.rawUserInfo == null && uri.rawQuery == null && uri.rawFragment == null) {
                "invalid endpoint"
            }
            require(uri.port == -1) { "invalid endpoint" }

            val rawHost = requireNotNull(uri.host) { "invalid endpoint" }
            require(!rawHost.endsWith('.')) { "invalid endpoint" }
            val host = IDN.toASCII(rawHost, IDN.USE_STD3_ASCII_RULES).lowercase()
            require(host.endsWith(".ts.net") && host.length > ".ts.net".length) {
                "invalid endpoint"
            }
            require(host.none { it == ':' } && host.any { it in 'a'..'z' }) { "invalid endpoint" }
            require(uri.rawAuthority.equals(host, ignoreCase = true)) { "invalid endpoint" }
            require(uri.rawPath in setOf("", "/", "/web/")) { "invalid endpoint" }

            TailnetOrigin("https://$host")
        }
    }

    internal fun resolve(route: EngineRoute): String = "$value${route.path}"
}
