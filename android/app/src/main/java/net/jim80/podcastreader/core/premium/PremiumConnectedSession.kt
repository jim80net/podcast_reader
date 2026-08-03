package net.jim80.podcastreader.core.premium

import java.time.Instant

internal sealed interface PremiumRestoreResult {
    data object Local : PremiumRestoreResult
    data class Online(val productState: ProductState) : PremiumRestoreResult
}

internal interface ConnectedPremiumSession {
    suspend fun restore(now: Instant, requestId: String): PremiumRestoreResult
    suspend fun signOut(requestId: String)
}

internal class ProductionPremiumConnectedSession(
    private val authorizer: PremiumAccountAuthorizer,
    private val nativeAuth: PremiumNativeAuthApi,
    private val currentUser: PremiumCurrentUserApi,
    private val entitlements: PremiumEntitlementApi,
) : ConnectedPremiumSession {
    override suspend fun restore(now: Instant, requestId: String): PremiumRestoreResult {
        when (authorizer.refreshOnce(nativeAuth, "$requestId-refresh")) {
            SessionMutationResult.Authorized -> Unit
            SessionMutationResult.Disconnected -> return PremiumRestoreResult.Local
            is SessionMutationResult.Failed -> return PremiumRestoreResult.Online(
                ProductState.ProductStateReducer.unavailable(OnlineUnavailableReason.INCOMPATIBLE_RESPONSE),
            )
        }
        val access = authorizer.currentAccessToken() ?: return PremiumRestoreResult.Local
        val subject = when (val result = currentUser.fetch(access, "$requestId-current-user")) {
            is CurrentUserFetchResult.Success -> result.subject
            is CurrentUserFetchResult.Failure -> return PremiumRestoreResult.Online(
                ProductState.ProductStateReducer.unavailable(result.failure.toUnavailableReason()),
            )
        }
        return when (val result = entitlements.fetch(access, "$requestId-entitlements")) {
            is EntitlementFetchResult.Success -> PremiumRestoreResult.Online(
                ProductState.ProductStateReducer.online(result.entitlement, subject, now),
            )
            is EntitlementFetchResult.Failure -> PremiumRestoreResult.Online(
                ProductState.ProductStateReducer.unavailable(result.failure.toUnavailableReason()),
            )
        }
    }

    override suspend fun signOut(requestId: String) {
        authorizer.revoke(nativeAuth, requestId)
    }
}

private fun PremiumFailure.toUnavailableReason(): OnlineUnavailableReason = when (category) {
    PremiumFailureCategory.NETWORK -> OnlineUnavailableReason.OFFLINE
    PremiumFailureCategory.UNAUTHORIZED -> OnlineUnavailableReason.UNAUTHORIZED
    PremiumFailureCategory.UNSAFE_ENDPOINT,
    PremiumFailureCategory.INCOMPATIBLE_RESPONSE,
    PremiumFailureCategory.SERVER -> OnlineUnavailableReason.INCOMPATIBLE_RESPONSE
}
