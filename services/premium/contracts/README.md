# Premium consumer API v1 contracts

The JSON files in this directory are committed consumer fixtures. The entitlement
fixtures freeze the complete v1 response shape, enum values, timestamp encoding,
and free versus premium capability meanings for desktop, extension, and Android.
`v1/entitlements/conformance-v1.json` is the executable validation language shared
by the backend, desktop, and Android: every named positive document must be accepted
and every named negative document must fail closed in all three consumers.

The `native-auth-v1-*` fixtures freeze device-authorization start, the shared
device-exchange/refresh token response, the sign-out revoke request and empty 204
response, and the five client state/error envelopes. Dynamic tokens, user codes,
subjects, timestamps, and request IDs use explicit fixture placeholders; parity
tests replace only those values and require every other live field byte-for-field.

`v1/current-user/` freezes the bearer-authenticated subject-binding response as
the opaque `id` alone. Email and verification status remain account-presentation
data: no v1 native consumer needs them, so strict clients are not coupled to or
required to decode unnecessary personal data.

`v1/boundary-policy/` is the single machine-readable admission policy for data
movement, retention, logging, public claims, and network-capable product roots.
Its generated per-surface projections are committed evidence, not independent
sources of truth; `python -m podcast_reader_premium.boundary_policy --check`
validates the policy and proves every projection is current.

Any incompatible field, enum, or semantic change requires a new
`schema_version` and an independent design gate. Additive server behavior must
remain parseable as one of these strict v1 documents until consumers explicitly
adopt a later contract.

`v1/ads/` freezes the house-only inventory response, empty-204 meaning, hostile
text handling, malformed fail-closed input, and forward-additive consumer case.

`v1/email/` freezes the bounded recipient-free relay request, DEV Maildir
success, byte-identical idempotent replay, and fixed delivery error envelopes.
