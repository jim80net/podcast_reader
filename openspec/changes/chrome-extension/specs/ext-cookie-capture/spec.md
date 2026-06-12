# ext-cookie-capture

## ADDED Requirements

### Requirement: On-demand, domain-scoped permission
Cookie capture SHALL be offered only from the failure context of a tracked job that ended with code `download_auth_required`, naming the target domain. Invoking it SHALL request, at that moment, the optional `cookies` permission together with origin permissions scoped to that domain only (`https://<domain>/*` and `https://*.<domain>/*`). Declining SHALL change nothing: no capture occurs and the extension retains only its manifest-time grants. The extension SHALL never request site origins at install time.

#### Scenario: Grant is requested at click time for one domain
- **WHEN** the user invokes "share your login" for a failed x.com job
- **THEN** Chrome's permission prompt covers the `cookies` permission and x.com origins only

#### Scenario: Decline is a clean no-op
- **WHEN** the user declines the permission prompt
- **THEN** no cookies are read, nothing is sent or stored, and the affordance remains available

### Requirement: Capture, push, and retain nothing
On grant, the extension SHALL read the domain's cookies via `chrome.cookies.getAll`, serialize them to Netscape cookie-jar format (including the `#HttpOnly_` prefix convention for httpOnly cookies), and `PUT /v1/cookies {domain, jar}` over the token-authed channel. Cookie values SHALL exist only transiently in popup memory during this transaction — never written to extension storage, never logged, never sent anywhere but the engine endpoint. After a successful push, the popup SHALL offer one-click resubmission of the failed source.

#### Scenario: Capture round-trip enables retry
- **WHEN** the user grants the permission and the push succeeds
- **THEN** the engine holds a jar for the domain and the popup offers to resubmit the failed job

#### Scenario: No extension-side cookie retention
- **WHEN** extension storage and logs are inspected after a capture
- **THEN** no cookie name, value, or jar content appears in either

#### Scenario: Push failure reports without leaking
- **WHEN** the engine rejects the jar (e.g. validation error)
- **THEN** the popup shows a self-authored error containing no cookie content, and nothing is retained
