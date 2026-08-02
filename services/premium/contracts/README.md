# Premium consumer API v1 contracts

The JSON files in this directory are committed consumer fixtures. The entitlement
fixtures freeze the complete v1 response shape, enum values, timestamp encoding,
and free versus premium capability meanings for desktop, extension, and Android.

The `native-auth-v1-*` fixtures freeze device-authorization start, the shared
device-exchange/refresh token response, the sign-out revoke request and empty 204
response, and the five client state/error envelopes. Dynamic tokens, user codes,
subjects, timestamps, and request IDs use explicit fixture placeholders; parity
tests replace only those values and require every other live field byte-for-field.

Any incompatible field, enum, or semantic change requires a new
`schema_version` and an independent design gate. Additive server behavior must
remain parseable as one of these strict v1 documents until consumers explicitly
adopt a later contract.
