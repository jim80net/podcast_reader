package net.jim80.podcastreader.core.premium

class PremiumAccountAuthorizer(
    private val credentialStore: PremiumCredentialStore,
) {
    @Volatile
    private var accessToken: PremiumAccessToken? = null

    @Synchronized
    fun installAuthorizedSession(
        credentials: PremiumAccountCredentials,
        token: PremiumAccessToken,
    ): Result<Unit> = credentialStore.save(credentials).onSuccess {
        accessToken = token
    }

    fun restoreAccountRecord(): Result<PremiumAccountCredentials?> = credentialStore.load()

    fun currentAccessToken(): PremiumAccessToken? = accessToken

    fun completeDeviceAuthorization(
        origin: PremiumOrigin,
        tokens: AuthorizedPremiumTokens,
    ): Result<Unit> = installAuthorizedSession(
        PremiumAccountCredentials(origin, tokens.refreshToken),
        tokens.accessToken,
    )

    @Synchronized
    fun refreshOnce(api: PremiumNativeAuthApi, requestId: String): SessionMutationResult {
        val credentials = credentialStore.load().getOrElse {
            accessToken = null
            return disconnectResult(requestId)
        } ?: run {
            accessToken = null
            return SessionMutationResult.Disconnected
        }
        return when (val result = api.refresh(credentials.refreshToken, requestId)) {
            is NativeAuthResult.Success -> result.value.validated().fold(
                onSuccess = { tokens ->
                    val rotated = PremiumAccountCredentials(credentials.origin, tokens.refreshToken)
                    credentialStore.save(rotated).fold(
                        onSuccess = {
                            accessToken = tokens.accessToken
                            SessionMutationResult.Authorized
                        },
                        onFailure = {
                            accessToken = null
                            disconnectResult(requestId)
                        },
                    )
                },
                onFailure = {
                    accessToken = null
                    disconnectResult(requestId)
                },
            )
            is NativeAuthResult.ProtocolError -> {
                accessToken = null
                disconnectResult(requestId)
            }
            is NativeAuthResult.Failure -> {
                accessToken = null
                disconnectResult(requestId)
            }
        }
    }

    @Synchronized
    fun revoke(api: PremiumNativeAuthApi, requestId: String): SessionMutationResult {
        accessToken = null
        val credentials = credentialStore.load().getOrElse {
            return disconnectResult(requestId)
        } ?: return disconnectResult(requestId)
        runCatching { api.revoke(credentials.refreshToken, requestId) }
        return disconnectResult(requestId)
    }

    @Synchronized
    fun disconnectLocalRecord(): Result<Unit> {
        accessToken = null
        return credentialStore.disconnectLocalRecord()
    }

    private fun disconnectResult(requestId: String): SessionMutationResult =
        credentialStore.disconnectLocalRecord().fold(
            onSuccess = { SessionMutationResult.Disconnected },
            onFailure = { SessionMutationResult.Failed(storageFailure(requestId)) },
        )
}

sealed interface SessionMutationResult {
    data object Authorized : SessionMutationResult
    data object Disconnected : SessionMutationResult
    data class Failed(val failure: PremiumFailure) : SessionMutationResult
}

private fun storageFailure(requestId: String) = PremiumFailure(
    PremiumFailureCategory.INCOMPATIBLE_RESPONSE,
    requestId = requestId,
)

data class PremiumRuntime(
    val authorizer: PremiumAccountAuthorizer,
    val entitlementTransport: PremiumEntitlementTransport,
)

object PremiumRuntimeGate {
    fun create(
        account: PremiumAccountCredentials?,
        factory: (PremiumAccountCredentials) -> PremiumRuntime,
    ): PremiumRuntime? = account?.let(factory)
}
