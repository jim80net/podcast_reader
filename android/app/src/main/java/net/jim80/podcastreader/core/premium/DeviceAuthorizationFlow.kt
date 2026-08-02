package net.jim80.podcastreader.core.premium

import android.content.Context
import android.content.Intent
import androidx.core.net.toUri
import java.time.Instant

data class DeviceAuthorizationSession(
    val origin: PremiumOrigin,
    internal val deviceCode: DeviceCode,
    val userCode: UserCode,
    val verificationUri: String,
    val expiresAt: Instant,
    val pollIntervalSeconds: Long,
    val nextPollAt: Instant,
) {
    override fun toString(): String = "DeviceAuthorizationSession(redacted)"
}

sealed interface NativeAuthResult<out T> {
    data class Success<T>(val value: T) : NativeAuthResult<T>
    data class ProtocolError(val error: NativeAuthErrorV1Dto) : NativeAuthResult<Nothing>
    data class Failure(val failure: PremiumFailure) : NativeAuthResult<Nothing>
}

interface PremiumNativeAuthApi {
    fun start(requestId: String): NativeAuthResult<DeviceAuthorizationStartV1Dto>
    fun poll(deviceCode: DeviceCode, requestId: String): NativeAuthResult<TokenResponseV1Dto>
    fun refresh(refreshToken: PremiumRefreshToken, requestId: String): NativeAuthResult<TokenResponseV1Dto>
    fun revoke(refreshToken: PremiumRefreshToken, requestId: String): NativeAuthResult<Unit>
}

fun interface ExternalBrowserLauncher {
    fun open(uri: String): Result<Unit>
}

class AndroidExternalBrowserLauncher(context: Context) : ExternalBrowserLauncher {
    private val applicationContext = context.applicationContext

    override fun open(uri: String): Result<Unit> = runCatching {
        applicationContext.startActivity(
            Intent(Intent.ACTION_VIEW, uri.toUri())
                .addCategory(Intent.CATEGORY_BROWSABLE)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK),
        )
    }
}

sealed interface DeviceAuthorizationTransition {
    data class Waiting(val session: DeviceAuthorizationSession) : DeviceAuthorizationTransition
    data class Authorized(val tokens: AuthorizedPremiumTokens) : DeviceAuthorizationTransition
    data object TooEarly : DeviceAuthorizationTransition
    data object Expired : DeviceAuthorizationTransition
    data object Denied : DeviceAuthorizationTransition
    data object Cancelled : DeviceAuthorizationTransition
    data class Failed(val failure: PremiumFailure) : DeviceAuthorizationTransition
}

class DeviceAuthorizationFlow(
    private val origin: PremiumOrigin,
    private val api: PremiumNativeAuthApi,
    private val browser: ExternalBrowserLauncher,
) {
    fun begin(now: Instant, requestId: String): DeviceAuthorizationTransition = when (val result = api.start(requestId)) {
        is NativeAuthResult.Success -> result.value.validated(origin, now).fold(
            onSuccess = { session ->
                browser.open(session.verificationUri).fold(
                    onSuccess = { DeviceAuthorizationTransition.Waiting(session) },
                    onFailure = { incompatible(requestId) },
                )
            },
            onFailure = { incompatible(requestId) },
        )
        is NativeAuthResult.Failure -> DeviceAuthorizationTransition.Failed(result.failure)
        is NativeAuthResult.ProtocolError -> incompatible(requestId)
    }

    fun poll(session: DeviceAuthorizationSession, now: Instant, requestId: String): DeviceAuthorizationTransition {
        if (!now.isBefore(session.expiresAt)) return DeviceAuthorizationTransition.Expired
        if (now.isBefore(session.nextPollAt)) return DeviceAuthorizationTransition.TooEarly
        return when (val result = api.poll(session.deviceCode, requestId)) {
            is NativeAuthResult.Success -> result.value.validated().fold(
                onSuccess = { DeviceAuthorizationTransition.Authorized(it) },
                onFailure = { incompatible(requestId) },
            )
            is NativeAuthResult.Failure -> DeviceAuthorizationTransition.Failed(result.failure)
            is NativeAuthResult.ProtocolError -> when (
                val code = runCatching { result.error.requireValid().code }.getOrElse { return incompatible(requestId) }
            ) {
                NativeAuthErrorCode.AUTHORIZATION_PENDING ->
                    waiting(session, now, session.pollIntervalSeconds, requestId)
                NativeAuthErrorCode.SLOW_DOWN -> runCatching {
                    Math.addExact(session.pollIntervalSeconds, RFC8628_SLOW_DOWN_SECONDS)
                }.fold(
                    onSuccess = { waiting(session, now, it, requestId) },
                    onFailure = { incompatible(requestId) },
                )
                NativeAuthErrorCode.EXPIRED_TOKEN -> DeviceAuthorizationTransition.Expired
                NativeAuthErrorCode.ACCESS_DENIED -> DeviceAuthorizationTransition.Denied
                NativeAuthErrorCode.REFRESH_TOKEN_REUSED -> incompatible(requestId)
            }
        }
    }

    fun cancel(session: DeviceAuthorizationSession): DeviceAuthorizationTransition {
        session.deviceCode.clear()
        return DeviceAuthorizationTransition.Cancelled
    }

    private fun waiting(
        session: DeviceAuthorizationSession,
        now: Instant,
        interval: Long,
        requestId: String,
    ): DeviceAuthorizationTransition = runCatching {
        DeviceAuthorizationTransition.Waiting(
            session.copy(pollIntervalSeconds = interval, nextPollAt = now.plusSeconds(interval)),
        )
    }.getOrElse { incompatible(requestId) }

    private fun incompatible(requestId: String) = DeviceAuthorizationTransition.Failed(
        PremiumFailure(PremiumFailureCategory.INCOMPATIBLE_RESPONSE, requestId = requestId),
    )
}

private const val RFC8628_SLOW_DOWN_SECONDS = 5L
