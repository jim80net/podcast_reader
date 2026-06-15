# app-setup-ui Specification (delta)

## ADDED Requirements

### Requirement: First-run chapter-provider onboarding
The first-run setup wizard SHALL present an optional chapter-provider step so a new user can enable chapter generation (and logical, idea-based paragraphs) without leaving onboarding. The step SHALL explain in plain language what an AI model provides, SHALL list the built-in providers plus the custom base-URL option (sourced from `GET /v1/providers`), SHALL let the user enter and test an API key (via the engine key-test round trip) and, on success, store it (`PUT /v1/keys`) and set it as the default provider (`PUT /v1/settings`), and SHALL reveal a base-URL field only when the custom provider is selected. The step SHALL be skippable: completing or skipping the wizard SHALL NOT require a key, and the absence of a key SHALL leave chapter generation simply disabled rather than blocking setup. The step SHALL introduce no new credential persistence — keys follow the existing in-memory-engine + vault model and SHALL NOT be written to engine disk or logs.

#### Scenario: Optional AI step is offered during setup
- **WHEN** the first-run wizard is shown
- **THEN** it presents an optional chapter-provider step explaining the benefit, with a provider selector (built-ins + custom) and a testable API-key field

#### Scenario: Setup completes without a key
- **WHEN** the user skips or finishes the wizard without entering a chapter API key
- **THEN** setup completes normally and chapter generation is simply inactive until a key is added (never a blocked wizard)

#### Scenario: Key is tested and stored
- **WHEN** the user enters a key and tests it successfully, then continues
- **THEN** the key is stored via the engine key channel and the chosen provider becomes the default, with no key material persisted to engine disk or logs

#### Scenario: Custom base URL appears only for the custom provider
- **WHEN** the user selects the custom provider
- **THEN** a base-URL field appears and is validated by the engine on save (https or http-on-localhost); other providers hide it
