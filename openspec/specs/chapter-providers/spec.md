# chapter-providers Specification

## Purpose
TBD - created by archiving change multi-provider-chapters. Update Purpose after archive.
## Requirements
### Requirement: Provider registry
The system SHALL define built-in chapter LLM provider defaults — `anthropic`, `openai`, `xai`, `openrouter`, `deepseek`, `custom` — and merge them with validated user-defined named OpenAI-compatible entries from `EngineSettings.custom_providers`. Each effective entry SHALL carry a base URL, default model, key-source environment variable name, and max-tokens cap. The global built-in mapping SHALL NOT be mutated; each effective registry and job request SHALL receive fresh configuration copies. User names SHALL be unique lowercase hyphenated slugs of at most 63 characters and SHALL NOT shadow a built-in name.

#### Scenario: Known providers resolvable
- **WHEN** any of the six provider names is looked up
- **THEN** a complete provider spec is returned

#### Scenario: Named provider resolvable
- **WHEN** settings define `office-gateway` and a request resolves that name
- **THEN** its configured URL, model, token cap, and `PODCAST_READER_PROVIDER_OFFICE_GATEWAY_KEY` key source are returned without mutating built-in defaults

#### Scenario: Custom URL validation
- **WHEN** the custom provider is configured with `http://evil.example.com`
- **THEN** the configuration is rejected; `https://…` and `http://127.0.0.1:…` are accepted

#### Scenario: Credential-bearing URL rejected
- **WHEN** a legacy or named custom URL contains userinfo, a query string, or a fragment
- **THEN** it is rejected before persistence or an outbound request, so credentials cannot hide in a URL field

### Requirement: Named provider request snapshot
The system SHALL validate and freshly copy named provider configuration into each `PipelineRequest`. A running request SHALL resolve its endpoint, default model, and max-tokens cap from that request-local snapshot, unaffected by subsequent settings edits.

#### Scenario: Settings change during a job
- **WHEN** a named provider is edited after its job request was built
- **THEN** that job keeps its original URL, model, and cap; the next job uses the edited entry

### Requirement: OpenAI-compatible generation
`generate_chapters` SHALL send a single non-streaming `POST {base_url}/chat/completions` with the existing system prompt and transcript, using the provided API key as a bearer token, and SHALL parse `choices[0].message.content` with the existing fence-stripping JSON parse. A `finish_reason` of `"length"` SHALL raise a truncation error. The `anthropic` package SHALL no longer be required.

#### Scenario: Successful generation
- **WHEN** the provider returns a valid JSON chapter array
- **THEN** the parsed chapters are returned, identical in shape to the current implementation

#### Scenario: Truncation raises
- **WHEN** the response's finish_reason is "length"
- **THEN** a truncation error is raised (handled by the existing chapters fault isolation)

#### Scenario: No anthropic import
- **WHEN** chapter generation runs with the `anthropic` package absent
- **THEN** it succeeds via plain HTTP

### Requirement: Key resolution and skip semantics
The CLI SHALL resolve the API key from the selected provider's environment variable (default provider `anthropic`, preserving `ANTHROPIC_API_KEY` behavior exactly); a `--provider` flag SHALL select the registry entry. When no key is available for the selected provider, the chapters step SHALL skip with the existing `chapters_skipped` warning, not fail.

#### Scenario: Anthropic env-var compatibility
- **WHEN** `ANTHROPIC_API_KEY` is set and no provider flag is given
- **THEN** chapters generate via the anthropic registry entry, as before

#### Scenario: Provider selection via flag
- **WHEN** `--provider deepseek` is passed and `DEEPSEEK_API_KEY` is set
- **THEN** the request goes to the DeepSeek base URL with that key

#### Scenario: Missing key skips
- **WHEN** the selected provider has no key available
- **THEN** the pipeline emits `chapters_skipped` with a provider-aware hint and renders a chapterless transcript

#### Scenario: Named provider CLI key
- **WHEN** `--provider office-gateway` selects a persisted named entry
- **THEN** the CLI resolves its key from `PODCAST_READER_PROVIDER_OFFICE_GATEWAY_KEY`

### Requirement: Model precedence
When no model is explicitly specified (CLI `--model` omitted; engine `chapter_model` empty), the selected provider's default model SHALL be used. An explicitly specified model SHALL be passed through verbatim. Switching providers without specifying a model SHALL never send another provider's model identifier.

#### Scenario: Provider flag without model flag
- **WHEN** `--provider deepseek` is passed without `--model`
- **THEN** the request uses the DeepSeek registry entry's default model

#### Scenario: Explicit model passes through
- **WHEN** `--provider openrouter --model meta-llama/llama-4-maverick` is passed
- **THEN** the request uses exactly that model identifier

### Requirement: Key redaction
API keys SHALL NOT appear in pipeline events, job records, journal entries, log output, or error messages produced by the chapters step. Error messages SHALL NOT include provider response bodies (the practical leak vector: auth-error bodies echo key fragments).

#### Scenario: Failure message contains no key material
- **WHEN** the provider request fails with an HTTP 401 whose response body echoes the key
- **THEN** neither the full key nor its first 12 characters appear in any emitted event, job record, or persisted file
