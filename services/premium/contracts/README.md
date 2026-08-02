# Entitlement API v1 contract

The JSON files in this directory are committed consumer fixtures. They freeze
the complete v1 response shape, enum values, timestamp encoding, and free versus
premium capability meanings for desktop, extension, and Android clients.

Any incompatible field, enum, or semantic change requires a new
`schema_version` and an independent design gate. Additive server behavior must
remain parseable as one of these strict v1 documents until consumers explicitly
adopt a later contract.
