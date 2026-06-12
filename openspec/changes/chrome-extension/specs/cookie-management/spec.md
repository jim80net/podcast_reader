# cookie-management

## ADDED Requirements

### Requirement: Cookie jar endpoints with metadata-only readback
The engine SHALL accept Netscape-format cookie jars via `PUT /v1/cookies` (`{domain, jar}`, bearer-authed), validating before storing: *domain* is a bare lowercase hostname; *jar* parses as Netscape cookie lines (including `#HttpOnly_`-prefixed entries); every cookie's domain field suffix-matches the declared domain; and the jar does not exceed the size cap. The jar SHALL be stored at `<data_dir>/cookies/<domain>.txt` via atomic write with owner-only permissions (0600; on Windows, user-profile ACLs as for `engine-state.json`), replacing any previous jar for that domain. `GET /v1/cookies` SHALL return metadata only — `[{domain, created_at}]`, never cookie values. `DELETE /v1/cookies/{domain}` SHALL remove the stored jar (404 if absent). Jar content SHALL appear in no API response, no log, and no diagnostic output.

#### Scenario: Valid jar stored owner-only
- **WHEN** a well-formed jar for `example.com` is PUT
- **THEN** `<data_dir>/cookies/example.com.txt` exists with mode 0600 and the exact jar content

#### Scenario: Foreign-domain cookies rejected
- **WHEN** a jar declared for `example.com` contains a cookie line whose domain field is `other.org`
- **THEN** the engine responds 400 and stores nothing

#### Scenario: Malformed jar rejected
- **WHEN** the jar body does not parse as Netscape cookie lines
- **THEN** the engine responds 400 and stores nothing

#### Scenario: Listing exposes no cookie values
- **WHEN** `GET /v1/cookies` is called after jars are stored
- **THEN** the response contains domains and timestamps only — no cookie names, values, or jar content

#### Scenario: Delete removes the jar
- **WHEN** `DELETE /v1/cookies/example.com` is called for a stored jar
- **THEN** the file is removed and the domain no longer appears in the listing

### Requirement: Jar-aware download step
At job dequeue, the engine job runner SHALL resolve the job source URL's host against stored jar domains by suffix match (host equals the domain, or ends with `.` + the domain) and, on a match, pass that jar's file path as the pipeline's cookies input — taking precedence over the `YT_DLP_COOKIES` environment fallback, which SHALL continue to apply when no jar matches. CLI one-shot behavior (env variable only) SHALL be unchanged.

#### Scenario: Matching jar feeds the download
- **WHEN** a jar is stored for `x.com` and an engine job runs for `https://x.com/user/status/1`
- **THEN** the download step invokes yt-dlp with `--cookies` pointing at the stored jar file

#### Scenario: Subdomain host matches the jar
- **WHEN** a jar is stored for `example.com` and a job source host is `media.example.com`
- **THEN** that jar is selected

#### Scenario: No match falls back to the environment
- **WHEN** no stored jar matches the source host and `YT_DLP_COOKIES` is set
- **THEN** the download uses the environment-specified cookies file, as before this change

### Requirement: Auth-required download failures carry a distinct code with face-appropriate hints
An authentication-required download failure SHALL fail the job with the distinct error code `download_auth_required`. The hint SHALL be authored by the execution face: engine jobs SHALL hint at sharing the login via the browser extension and at importing a cookies file, while CLI runs SHALL keep the `YT_DLP_COOKIES` hint. No hint SHALL ever recommend `--cookies-from-browser` for Chrome on Windows (per the parent design's F2/N2).

#### Scenario: Engine job hints at the extension
- **WHEN** an engine job's download fails on an authentication error
- **THEN** the job fails with code `download_auth_required` and a hint referencing extension cookie sharing and cookies-file import

#### Scenario: CLI keeps the env hint
- **WHEN** a CLI one-shot download fails on an authentication error
- **THEN** the printed hint references `YT_DLP_COOKIES`, as today
