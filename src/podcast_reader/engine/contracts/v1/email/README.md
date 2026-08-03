# Transcript email engine contract v1

These fixtures freeze the complete main-to-engine boundary for local transcript
email delivery. `online-capability.json` is a separate memory-only capability;
it does not extend the frozen subscription capability. `claim.json` is the only
shape that carries transcript content out of the engine, and
`completion.json`/`release.json` close that exact lease generation.

`manual-create.json` freezes the idempotent explicit-action input. The engine
creates the relay client delivery ID and never accepts a recipient. Consumers
must reject missing, renamed, or additional fields. Evolution requires a new
schema version and fixtures.
