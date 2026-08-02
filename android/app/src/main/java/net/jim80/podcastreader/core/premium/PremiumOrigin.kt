package net.jim80.podcastreader.core.premium

import java.net.URI

@JvmInline
value class PremiumOrigin private constructor(internal val value: String) {
    override fun toString(): String = "PremiumOrigin(redacted)"

    internal fun resolve(route: PremiumRoute): String = "$value${route.path}"

    companion object {
        fun fromTrustedConfiguration(value: String): Result<PremiumOrigin> = runCatching {
            require(value.isNotEmpty() && value == value.trim() && value.length <= 2048) {
                "invalid premium origin"
            }
            val uri = URI(value)
            require(uri.scheme == "https" && uri.rawUserInfo == null) { "invalid premium origin" }
            require(uri.rawPath.isNullOrEmpty() && uri.rawQuery == null && uri.rawFragment == null) {
                "invalid premium origin"
            }
            val host = requireNotNull(uri.host) { "invalid premium origin" }
            require(host == host.lowercase() && !host.endsWith('.') && host.all { it.code in 33..126 }) {
                "invalid premium origin"
            }
            require(uri.port == -1 || uri.port in 1..65535 && uri.port != 443) {
                "invalid premium origin"
            }
            val canonicalHost = if (host.contains(':')) "[$host]" else host
            val canonical = buildString {
                append("https://")
                append(canonicalHost)
                if (uri.port != -1) append(":${uri.port}")
            }
            require(value == canonical) { "invalid premium origin" }
            PremiumOrigin(canonical)
        }
    }
}
