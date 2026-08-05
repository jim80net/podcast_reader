# Screenshot drift waivers

The landing-page check requires UI-affecting pull requests to refresh
`provenance.json` from an exact-main installed walkthrough or modify this file
with an explicit waiver for independent review.

Each waiver must name the changed UI scope, explain why the published screenshots
remain accurate, and identify the follow-up issue or expiry condition. There are
currently no active waivers.

## PR #190 — synthetic path fixtures

- Scope: `app/src/renderer/src/job-view.test.ts` and
  `app/src/renderer/src/settings-form.test.ts`.
- Reason: the changes replace user-specific fixture paths with synthetic identities;
  production renderer code, rendered copy, layout, and screenshot pixels are unchanged.
- Review: independent review is required before merge.
- Expiry: this waiver applies only to PR #190 and expires when that PR merges. No
  follow-up issue is required because the affected files are test fixtures only.
