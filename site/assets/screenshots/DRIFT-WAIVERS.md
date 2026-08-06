# Screenshot drift waivers

The landing-page check requires UI-affecting pull requests to refresh
`provenance.json` from an exact-main installed walkthrough or modify this file
with an explicit waiver for independent review.

Each waiver must name the changed UI scope, explain why the published screenshots
remain accurate, and identify the follow-up issue or expiry condition. Waiver
records are listed below; a record is active only until its stated expiry condition.

## PR #190 — synthetic path fixtures

- Scope: `app/src/renderer/src/job-view.test.ts` and
  `app/src/renderer/src/settings-form.test.ts`.
- Reason: the changes replace user-specific fixture paths with synthetic identities;
  production renderer code, rendered copy, layout, and screenshot pixels are unchanged.
- Review: independent review is required before merge.
- Expiry: this waiver applies only to PR #190 and expires when that PR merges. No
  follow-up issue is required because the affected files are test fixtures only.

## Issue #194 — wizard clearance implementation stage

- Scope: `app/src/renderer/src/views/setup.ts`, `app/src/renderer/src/style.css`,
  `app/tests/e2e/packs.spec.ts`, and `app/tests/install/walkthrough.mjs`.
- Reason: the renderer change must merge before an installed walkthrough can truthfully
  capture it from exact main. The currently published first-run frame remains historical
  evidence and is known to show the model-row overlap this change fixes.
- Review: independently review the measured clearance and its 100%/125% Light/Dark
  negative and positive-control geometry gates before merging the implementation stage.
- Expiry: issue #194 remains open. Immediately after this stage merges, run the installed
  walkthrough at exact main, refresh the 24-frame metadata and affected published assets,
  and remove this waiver in the separately reviewed provenance stage.
