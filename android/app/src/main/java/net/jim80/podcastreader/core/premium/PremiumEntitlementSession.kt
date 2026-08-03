package net.jim80.podcastreader.core.premium

class PremiumEntitlementSession(
    private val authorizer: PremiumAccountAuthorizer,
    private val entitlementTransport: PremiumEntitlementApi,
    private val nativeAuthApi: PremiumNativeAuthApi,
) {
    fun fetch(requestId: String): EntitlementFetchResult {
        val token = authorizer.currentAccessToken() ?: return unauthorized(requestId)
        val first = entitlementTransport.fetch(token, requestId)
        if (first !is EntitlementFetchResult.Failure || first.failure.category != PremiumFailureCategory.UNAUTHORIZED) {
            return first
        }
        return when (val refreshed = authorizer.refreshOnce(nativeAuthApi, requestId)) {
            SessionMutationResult.Authorized -> authorizer.currentAccessToken()?.let {
                entitlementTransport.fetch(it, requestId)
            } ?: unauthorized(requestId)
            SessionMutationResult.Disconnected -> unauthorized(requestId)
            is SessionMutationResult.Failed -> EntitlementFetchResult.Failure(refreshed.failure)
        }
    }

    private fun unauthorized(requestId: String) = EntitlementFetchResult.Failure(
        PremiumFailure(PremiumFailureCategory.UNAUTHORIZED, httpStatus = 401, requestId = requestId),
    )
}
