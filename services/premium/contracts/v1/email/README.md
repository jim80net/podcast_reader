# Transcript email relay v1 fixtures

These backend-owned fixtures freeze `POST /v1/email-deliveries` before any
desktop consumer is implemented. Requests contain one bounded plain-text
transcript and no recipient, account email, source URL, feed data, local path,
HTML, attachment, engine bearer, or provider field.

`delivered.json` is the first successful DEV Maildir result.
`idempotent-replay.json` is deliberately byte-for-byte identical: retrying the
same client delivery ID and canonical payload returns the original delivery
without creating another message or widening the response contract.

`errors.json` freezes the feature, size, idempotency, sink, and future verified-
address failures in the service's bounded error envelope. The verified-address
failure is reserved for a separately gated production sink; DEV always derives
its fixed `.invalid` destination from configuration and never accepts a
recipient.

Any incompatible field, enum, bound, or semantic change requires a new
`schema_version` and an independent design gate. Consumers must read these
files directly rather than maintaining copied lookalikes.
