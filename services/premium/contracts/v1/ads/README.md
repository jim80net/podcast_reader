# House inventory v1 consumer fixtures

`eligible-library.json` and `eligible-reader.json` are complete valid response
fixtures for `GET /v1/ads/inventory/{slot}`. Dynamic revisions and expiry are
normalized in live parity tests; every other field is exact.

`no-content.json` freezes the meaning of an empty 204 response. `hostile-text.json`
is valid inventory whose strings must render only as inert native text.
`malformed.json` must fail closed because it carries an unknown creative kind and
non-HTTPS CTA. `forward-additive.json` proves consumer tolerance for unknown
members without granting behavior for unknown consumed values.

Incompatible fields, enums, or semantics require a new `schema_version` and an
independent design gate. Consumers must not edit or fork these backend-owned files.
