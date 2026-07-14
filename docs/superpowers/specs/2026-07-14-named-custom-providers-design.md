# Named OpenAI-Compatible Providers — Phase 1 Design

**Status:** design review pending  
**Issue:** #10, phase 1 only

## Context

Podcast Reader currently has five built-in OpenAI-compatible chapter providers
plus one legacy `custom` slot. The slot has one URL, one default max-token cap,
and one credential identity. Users therefore cannot keep a local model, a
corporate gateway, and a hosted compatible service configured at the same time.

The existing security boundary is sound and remains the basis of this change:
the engine persists nonsecret settings, Electron persists credentials encrypted
with `safeStorage`, and the engine receives credentials through `PUT /v1/keys`
into a process-memory dictionary. Headless use falls back to environment
variables. Provider credentials are write-only at every engine API boundary.

## Goals

- Let a user add, edit, select, test, and remove any number of named
  OpenAI-compatible provider entries in Settings.
- Give every entry its own base URL, default model, max-token cap, and key
  identity while persisting only the first three plus its name.
- Treat the existing `PROVIDERS` mapping as built-in defaults and resolve an
  effective registry from built-ins plus the current settings.
- Let one-shot CLI users select the same named entries with `--provider` and
  supply their credentials through a deterministic per-name environment
  variable.
- Preserve settings snapshotting, missing-key chapter skip behavior, legacy
  `custom` behavior, and the K4 credential-redaction guarantees.

## Non-goals

- OAuth, device-code, PKCE, token refresh, or any auth-method UI. Those are
  issue #10 phase 2 and require a separate product/design decision.
- Changing the OpenAI-compatible `/chat/completions` transport or adding
  provider-specific request adapters.
- Restyling Settings or onboarding. This change reuses the current provider,
  model, URL, key, and key-test controls and adds only the entry-management
  fields/actions they need.
- Import/export or cloud synchronization of provider configuration or keys.

## Decisions

### 1. Persist a nonsecret `custom_providers` list

`EngineSettings` gains:

```text
custom_providers: [
  {name: str, base_url: str, default_model: str, max_tokens: int},
  ...
]
```

No entry has a `key`, `api_key`, credential fingerprint, or other secret-derived
field. `default_settings()` supplies an empty list; old files therefore upgrade
by the existing defaults merge. `SettingsBody` makes the field optional so old
clients retain the current list on partial updates.

The list, rather than an object keyed by name, keeps the public API explicit and
easy for the Settings form to edit. Before persistence the engine validates the
whole candidate list, rejects duplicates, and writes the canonical validated
shape. Both the nested provider model and `SettingsBody` use Pydantic
`extra="forbid"`; unknown keys—including `key` and `api_key`—are rejected rather
than ignored or silently persisted.

`load_settings()` also validates and freshly canonicalizes the complete list.
If a manually edited, downgrade-written, or otherwise malformed entry is found,
the whole settings file is quarantined as `settings.json.corrupt` and defaults
are returned, matching the existing malformed-settings recovery contract. A
bad entry therefore cannot make provider listing, job dequeue, or CLI startup
fail repeatedly. This deliberately favors a serving engine with visible default
configuration over retaining a partly trustworthy file.

The legacy `custom` entry and `custom_provider_url` remain supported. Removing
them would break existing settings, CLI invocations, rerun overrides, and vault
entries. A user who needs multiple endpoints uses named entries; no automatic
migration is required.

### 2. Names are stable lowercase slugs

A user-defined provider name must match:

```text
[a-z][a-z0-9]*(?:-[a-z0-9]+)*
```

with a maximum length of 63 characters. It must not equal a built-in provider
name. Names are identities, not mutable display labels: renaming is modeled as
adding the new entry and removing the old one. This deliberately modest policy
makes names safe in the CLI, API errors, vault map, and environment variables,
and makes the environment mapping collision-free. UI copy names the constraint
and uses examples such as `opencode-zen` and `office-gateway`.

For a name `office-gateway`, the headless/CLI key variable is:

```text
PODCAST_READER_PROVIDER_OFFICE_GATEWAY_KEY
```

Only hyphens are converted to underscores and lowercase ASCII is converted to
uppercase. Because underscores are not valid in names, two valid names cannot
map to the same variable. Built-ins retain their established variables, and the
legacy `custom` slot retains `PODCAST_READER_CUSTOM_PROVIDER_KEY`.

### 3. The effective registry is a pure merge

`providers.py` keeps `PROVIDERS` as the immutable-in-practice built-in defaults
and adds pure helpers to:

1. validate/canonicalize a `CustomProviderConfig`,
2. derive its `ProviderSpec`, including the per-name key variable, and
3. build/resolve an effective registry from `PROVIDERS` plus a supplied list.

The global mapping is never mutated. Every effective-registry call returns fresh
`ProviderSpec` dictionaries, including fresh copies of built-ins, and every
request snapshot contains freshly canonicalized provider-entry dictionaries
rather than references into the loaded settings object. Request handlers and
job runners build from their current settings (or settings snapshot), so tests
remain isolated and caller mutation or a settings change cannot leak across
engine instances or in-flight jobs. User entries cannot shadow built-ins. All
existing membership checks (`PUT /v1/settings`, job overrides,
`PUT /v1/keys`, key test, provider listing, and dequeue-time key lookup) use the
effective registry rather than the closed built-in map.

`GET /v1/providers` returns built-ins followed by user entries in settings order.
Its existing response remains sufficient: `id`, `default_model`, and the
non-secret `key_available` boolean. Configuration details come from
`GET /v1/settings`; provider listing does not gain a secret-derived field.

Clearing a key remains permitted even just after its custom entry was removed;
setting or testing a non-empty key still requires a currently known provider.
This lets Electron remove an orphaned vault entry without weakening outbound
request validation.

### 4. URL validation remains HTTPS-or-loopback HTTP

**Reviewed policy decision:** every user-defined base URL uses the current
`validate_custom_url` transport policy, strengthened to keep credentials out of
persisted URL fields:

- accept `https://` endpoints with a hostname;
- accept plaintext `http://` only for `localhost`, `127.0.0.1`, or `::1`;
- reject remote plaintext HTTP, missing hostnames, and other schemes; and
- reject URL username/password components, query strings, and fragments. Normal
  ports and path components remain valid.

Arbitrary means arbitrary compatible endpoint ownership, not arbitrary
transport security. A bearer credential sent to remote HTTP is observable on
the network, so allowing it would turn a configuration typo into credential
disclosure. Loopback HTTP remains necessary for llama.cpp and similar local
servers. Userinfo and query/fragment fields are rejected because credentials
placed there would otherwise be persisted in `settings.json` and could appear
in logs or errors despite never entering an `api_key` field. The strengthened
rule applies to both named entries and the legacy `custom` slot. The engine
enforces it when settings are saved and resolves through the same validator
again before any outbound request. The UI may provide early feedback, but
server validation is authoritative.

We do not add host allowlists or network egress restrictions: the user is
explicitly configuring an endpoint on their own machine, and corporate/private
network HTTPS endpoints are a core use case. We also do not probe URLs during a
settings save; the existing explicit key-test action is the network operation.

### 5. Snapshot configuration with each pipeline request

`PipelineRequest` gains `custom_providers`, carrying only the validated
nonsecret list. The engine runner copies the list from the dequeue-time settings
snapshot; the CLI copies it from the settings file. `pipeline.py` resolves the
selected provider against that request-local list. This preserves the invariant
that an in-flight job cannot switch endpoints, models, or caps because Settings
changed.

The engine resolves the key at dequeue from, in order, the pushed in-memory key
and the effective provider spec's environment variable. The key remains a
separate `chapter_api_key` argument and never enters the settings snapshot,
journal, job record, or event payload.

Deleting the active provider is rejected unless the same settings update also
selects a provider that remains in the effective registry. Deletion is also
rejected while **any nonterminal job** has a `chapter_provider` override naming
that entry; those overrides are journaled but do not carry provider
configuration. This includes `running`: the store exposes that state just before
the runner loads its settings snapshot, so treating it as already snapshotted
would leave a deletion race. The error tells the user which job must finish or
be discarded first; terminal jobs do not block deletion. This produces one
deterministic outcome across confirmation, running-state timing, and engine
restart instead of making dequeue depend on a stale key. Rerun overrides must
name a provider in the registry effective at submission and again at dequeue as
a defensive check.

### 6. CLI reads named definitions, credentials only from the environment

Argparse stops using the closed `choices=PROVIDERS` list. At invocation, the
one-shot CLI reads nonsecret provider definitions from
`<PODCAST_READER_DATA_DIR>/settings.json` through `load_settings(data_dir_path())`
without creating the directory. It resolves `--provider` against built-ins plus
that list and reports an argparse-style error before pipeline work for unknown
or invalid names. Its default remains `anthropic`.

The CLI never attempts to read Electron `safeStorage` or the running engine's
memory. For a named entry it reads only the per-name environment variable from
Decision 2. This keeps headless behavior explicit and avoids a second vault
decryption surface. The legacy custom URL environment variable continues to
override/provide the old `custom` slot as today.

### 7. Settings reuses the existing provider controls

The Settings provider section gains an `Add provider` action and edit/remove
actions for user-defined entries. The editor reuses the visual treatment of the
existing URL, model, password key field, and key-test controls, adding name and
max-tokens inputs. Its **default model** belongs to the named entry. This is
separate from the existing **Chapter model override**, which remains an optional
override for the currently selected provider. Built-ins are selectable but not
editable or removable. The single legacy `custom` slot keeps its existing URL
behavior.

When the user changes the selected provider in Settings, the UI clears the
Chapter model override to `""` before save, so the newly selected provider uses
its own default unless the user deliberately enters a new override afterward.
At resolution, blank `chapter_model` always selects the effective provider
spec's `default_model`; a nonblank `chapter_model` wins verbatim. Editing a named
entry's default never rewrites a nonblank active override, and the UI labels the
two fields **Provider default model** and **Chapter model override** rather than
presenting two ambiguous “model” inputs. Removing an entry removes its default
but does not copy that value into the replacement provider's override.
For API compatibility, a partial `PUT /v1/settings` that changes only the
provider continues to retain an existing explicit override; the desktop client
authors the safer clear explicitly.

Saving a named entry first persists the nonsecret settings through
`PUT /v1/settings`, then pushes a non-empty entered key through the existing
vault-and-`PUT /v1/keys` path. Clicking **Test key** on a new or edited entry has
an explicit side effect: the UI validates and persists the nonsecret draft,
then tests the key against that persisted snapshot. The UI says “Save provider
and test key” while the draft is dirty. If settings validation/save fails, no
network test or key write occurs. If the test fails, the provider edit remains
saved but the entered key remains unstored. Entry edit/remove actions are
disabled for the duration of the test, and the handler snapshots the provider
name, so a later selection change cannot route the result or key to another
entry. This avoids inventing a credential-bearing test payload that duplicates
provider configuration.

Removing an entry updates settings and then clears that provider from the app
vault/engine through the existing empty-key operation. If the provider was
selected, the UI includes a switch to `anthropic` in the same settings update.
Failures remain visible inline; there is no silent partial success.

Setup/onboarding and rerun dialogs consume `GET /v1/providers`, so named entries
automatically become selectable there. They do not gain provider-management UI;
management stays in Settings.

## Credential and redaction invariants

- `settings.json`, `EngineSettings`, `CustomProviderConfig`, provider list
  responses, jobs, events, journals, discovery files, and logs contain no key
  or key-derived material.
- Electron may persist a named key only through the existing encrypted
  `safeStorage` vault; session-memory fallback behavior is unchanged.
- The engine holds named keys only in its process-memory map. Restart clears
  them until Electron pushes again or an environment fallback is present.
- Provider/network exceptions remain sanitized exactly as built-ins are today;
  response bodies never enter user-visible errors or logs.
- K4 sweep tests insert distinctive credentials for at least two named
  providers, exercise push, test, job, listing, and persistence paths, then
  scan all endpoint responses and engine-owned files for the complete values
  and identifying prefixes.

## Validation details

- `base_url`: policy in Decision 4, with explicit tests proving userinfo,
  query-string, and fragment credentials are rejected and never persisted.
- `default_model`: trimmed, non-empty, maximum 256 characters.
- `max_tokens`: integer from 1 through 1,000,000. The broad upper bound prevents
  accidental/unbounded payloads without encoding assumptions about future
  providers; the remote service remains authoritative about its actual limit.
- list length: at most 100 entries, preventing an accidentally enormous
  settings payload while remaining far above a realistic desktop use case.

Validation errors name the entry and field but never include credential values
(credentials are absent from this payload by construction).

## Test plan (TDD order)

1. Provider unit tests: valid merge/resolution, ordering, reserved/duplicate and
   malformed names, collision-free env derivation, URL policy, model/token
   validation, and no global-registry mutation.
2. Settings tests: stale-file default, validated round-trip, malformed-list
   load quarantine with successful subsequent startup/list/job/CLI behavior,
   extra-field and credential-shaped-field rejection, and serialization
   containing no secret-shaped fields.
3. Engine API tests: list/select/push/test/remove named providers, env and pushed
   key precedence, active-provider deletion rejection, rejection while queued,
   awaiting-confirmation, or running overrides refer to the entry (including a
   running-transition race and restart), request snapshot behavior, and
   unknown-provider rejection against the effective registry.
4. Extend K4 sweeps with distinctive named-provider keys across engine files,
   responses, journal/events, and captured logs.
5. Pipeline/CLI tests: named endpoint/model/token propagation, per-name env key,
   missing key skip, unknown provider early error, legacy built-ins/custom
   unchanged, and no data-dir creation for a read-only CLI invocation.
6. Renderer/main tests: CRUD form planning, separation of entry default model
   from active model override, override clear on provider switch, named option
   selection, explicit save-then-test routing (save rejection, test failure, and
   attempted deletion during an in-flight test), vault push after restart,
   deletion clear, and unchanged safeStorage fallback. Update real-engine
   mirror/smoke assertions for the new settings field.
7. Run Python unit/integration tests, strict mypy, Ruff, renderer unit tests,
   TypeScript checks, and the applicable app E2E/smoke gates.

Tests are written failing-first at each layer before implementation for that
layer. Review evidence will distinguish simulated engine/API proof from live
provider compatibility, which remains user-configured and cannot be proven in
CI without an external service and credential.

## Specification updates

Implementation includes updates to the canonical OpenSpec requirements, not
only this design document:

- `chapter-providers` changes from an exactly-six closed registry to built-in
  defaults plus validated named entries, including URL/name/model/token rules
  and request-snapshot resolution.
- `key-management` defines nonsecret persisted provider configuration,
  per-name environment fallback, dynamic key API/listing behavior, and K4
  coverage for named credentials while retaining memory-only engine storage.
- Relevant engine/app-view requirements describe effective-registry validation
  and Settings CRUD behavior. No specification introduces OAuth in phase 1.

## Alternatives considered

- **Mutate the global `PROVIDERS` dict after settings load:** rejected because
  engine instances/tests would share mutable configuration and job snapshots
  could silently resolve against later state.
- **Replace the legacy `custom` slot immediately:** rejected as unnecessary
  breakage to settings, CLI environment variables, reruns, and stored vault
  identities.
- **Allow remote HTTP behind a warning:** rejected because warnings do not
  prevent bearer-key disclosure and the existing fail-closed policy already
  establishes the safer contract.
- **Store key environment-variable names in settings:** rejected because names
  can reference unrelated process secrets and make configuration less portable;
  the deterministic convention is sufficient.
- **Pass URL/model/token cap in every key-test request:** rejected because it
  creates a second provider-config API and complicates validation. Persist the
  nonsecret entry first, then use the existing credential test.

## Migration and rollback

The forward migration is additive. Existing settings load with
`custom_providers=[]`, built-in and legacy `custom` semantics remain unchanged,
and existing vault entries retain their identities.

Downgrade is credential-safe but not configuration-lossless: an older engine
can initially load a file containing the unknown top-level field, but its next
settings save reconstructs the old known shape and drops `custom_providers`.
Electron vault ciphertext remains keyed by provider name and is not exposed or
deleted. Re-upgrading restores named definitions only if the user restores the
pre-downgrade settings backup or re-enters their nonsecret URL/model/cap; the
stored key can then be pushed again. Lossless old-version round-tripping would
require a general unknown-field preservation scheme and is outside this phase.

## Open questions

None for phase 1. OAuth provider eligibility, browser/device flow, refresh-token
storage, subscription-policy constraints, and fallback behavior are explicitly
deferred to a separate phase-2 design fork.
