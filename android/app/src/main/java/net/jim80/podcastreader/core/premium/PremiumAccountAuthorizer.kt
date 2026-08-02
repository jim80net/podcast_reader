package net.jim80.podcastreader.core.premium

class PremiumAccountAuthorizer(
    private val credentialStore: PremiumCredentialStore,
) {
    @Volatile
    private var accessToken: PremiumAccessToken? = null

    fun installAuthorizedSession(
        credentials: PremiumAccountCredentials,
        token: PremiumAccessToken,
    ): Result<Unit> = credentialStore.save(credentials).onSuccess {
        accessToken = token
    }

    fun restoreAccountRecord(): Result<PremiumAccountCredentials?> = credentialStore.load()

    fun currentAccessToken(): PremiumAccessToken? = accessToken

    fun disconnectLocalRecord(): Result<Unit> {
        accessToken = null
        return credentialStore.disconnectLocalRecord()
    }
}

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
