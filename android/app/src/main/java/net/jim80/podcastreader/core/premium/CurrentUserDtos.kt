package net.jim80.podcastreader.core.premium

import kotlinx.serialization.Serializable

@Serializable
data class CurrentUserV1Dto(
    val id: String,
) {
    override fun toString(): String = "CurrentUserV1Dto(redacted)"

    internal fun validatedSubject(): Result<String> = runCatching {
        require(id.isNotBlank() && id == id.trim() && id.length <= MAX_CURRENT_USER_SUBJECT_LENGTH) {
            "invalid current-user subject"
        }
        id
    }
}

private const val MAX_CURRENT_USER_SUBJECT_LENGTH = 40
