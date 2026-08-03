package net.jim80.podcastreader.core.premium

import java.io.IOException
import kotlinx.serialization.SerializationException
import okhttp3.OkHttpClient
import okhttp3.Request

class PremiumNativeAuthTransport(
    private val requestFactory: PremiumRequestFactory,
    private val client: OkHttpClient = securePremiumHttpClient(),
) : PremiumNativeAuthApi {
    override fun start(requestId: String): NativeAuthResult<DeviceAuthorizationStartV1Dto> =
        execute(requestFactory.deviceStart(), 201, requestId)

    override fun poll(deviceCode: DeviceCode, requestId: String): NativeAuthResult<TokenResponseV1Dto> =
        execute(requestFactory.devicePoll(deviceCode), 200, requestId)

    override fun refresh(refreshToken: PremiumRefreshToken, requestId: String): NativeAuthResult<TokenResponseV1Dto> =
        execute(requestFactory.refresh(refreshToken), 200, requestId)

    override fun revoke(refreshToken: PremiumRefreshToken, requestId: String): NativeAuthResult<Unit> = try {
        client.newCall(requestFactory.revoke(refreshToken)).execute().use { response ->
            when {
                response.code == 204 && response.body.readBounded().isEmpty() ->
                    NativeAuthResult.Success(Unit)
                response.code in PROTOCOL_ERROR_STATUSES -> decodeError(response.body.readBounded(), requestId, response.code)
                else -> NativeAuthResult.Failure(PremiumFailureMapper.fromHttp(response.code, requestId))
            }
        }
    } catch (_: IOException) {
        networkFailure(requestId)
    } catch (_: RuntimeException) {
        incompatibleFailure(requestId)
    }

    private inline fun <reified T> execute(
        request: Request,
        expectedStatus: Int,
        requestId: String,
    ): NativeAuthResult<T> = try {
        client.newCall(request).execute().use { response ->
            when {
                response.code == expectedStatus -> {
                    val body = response.body.readBounded()
                    NativeAuthResult.Success(premiumJson.decodeFromString<T>(body))
                }
                response.code in PROTOCOL_ERROR_STATUSES -> decodeError(response.body.readBounded(), requestId, response.code)
                else -> NativeAuthResult.Failure(PremiumFailureMapper.fromHttp(response.code, requestId))
            }
        }
    } catch (_: IOException) {
        networkFailure(requestId)
    } catch (_: SerializationException) {
        incompatibleFailure(requestId)
    } catch (_: RuntimeException) {
        incompatibleFailure(requestId)
    }

    private fun decodeError(body: String, requestId: String, status: Int): NativeAuthResult<Nothing> = try {
        NativeAuthResult.ProtocolError(
            premiumJson.decodeFromString<NativeAuthErrorV1Dto>(body).requireValid(),
        )
    } catch (_: RuntimeException) {
        NativeAuthResult.Failure(PremiumFailureMapper.fromHttp(status, requestId))
    }
}

private fun networkFailure(requestId: String) = NativeAuthResult.Failure(
    PremiumFailure(PremiumFailureCategory.NETWORK, requestId = requestId),
)

private fun incompatibleFailure(requestId: String) = NativeAuthResult.Failure(
    PremiumFailure(PremiumFailureCategory.INCOMPATIBLE_RESPONSE, requestId = requestId),
)

private val PROTOCOL_ERROR_STATUSES = setOf(400, 401, 403, 409)
