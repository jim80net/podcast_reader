# Production dependency audit

CI runs `npm run audit:production` after `npm ci`. The gate audits only installed
runtime dependencies (`npm audit --omit=dev`) and rejects every high- or
critical-severity finding.

Development-only packages are outside this gate because they are not packaged
into the application runtime. They remain visible in the full `npm audit`
report and are handled through normal dependency updates and security review;
their exclusion here must not be represented as a clean full-tree audit.

An unavoidable production advisory may be temporarily excepted only by adding
an exact entry to `npm-audit-exceptions.json` in a reviewed pull request. Every
entry must name the affected package and advisory, link a public tracking issue,
give a non-empty reason, and carry an ISO date after which CI rejects it. Unknown,
expired, duplicate, malformed, and no-longer-used exceptions all fail closed.

Example shape (the committed registry is intentionally empty):

```json
{
  "package": "example-package",
  "advisory": "GHSA-xxxx-xxxx-xxxx",
  "issue": "https://github.com/jim80net/podcast_reader/issues/123",
  "reason": "No patched version is compatible; removal is tracked.",
  "expires": "2026-09-01"
}
```
