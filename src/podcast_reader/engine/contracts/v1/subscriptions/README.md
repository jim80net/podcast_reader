# Subscription engine contract v1

`online-capability.json` freezes the complete desktop-to-engine capability
snapshot. Consumers must reject missing, renamed, or additional fields. The
snapshot is memory-only: the engine never writes its subject, revisions, or
expiry to the subscription database.

Changing an existing field is a breaking contract change. Additive evolution
requires a new schema version and fixture.
