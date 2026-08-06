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
- Expiry: PR #196 has merged, but this record remains only until the required exact-main
  24-frame metadata and affected published assets are refreshed after PR #198.

## PR #198 — installed wizard capture visibility gate

- Scope: `app/tests/install/walkthrough.mjs` only.
- Reason: this is a test-only capture-gate correction; production renderer code and pixels
  are unchanged. Exact-main run 31070139538 was deliberately withheld because the prior
  gate admitted a first-run frame whose action bar was outside the viewport.
- Review: independently review the positive-height and full-viewport action bounds while
  retaining measured clearance and model-row/action non-overlap checks.
- Expiry: PR #198 has merged, but run 31070934606 exposed the capture-coordinate defect
  covered by PR #199. Remove this record with the validated post-#199 provenance refresh.

## PR #199 — scrolled viewport screenshot coordinates

- Scope: `app/tests/install/capture-evidence.mjs` and its focused unit test only.
- Reason: this is test-evidence infrastructure; production renderer code and pixels are
  unchanged. Exact-main run 31070934606 was deliberately withheld because its geometry
  gate evaluated a scrolled viewport while the raw screenshot clip remained at document
  origin, so the PNG did not depict the gated state.
- Review: independently verify that viewport captures use scroll coordinates, full-page
  captures remain origin-based, and the focused regression exercises nonzero offsets.
- Expiry: when PR #199 merges, immediately run the installed walkthrough at the resulting
  exact main, publish validated 24/24 metadata and affected assets in a separately reviewed
  provenance PR, and remove all active Issue #194, PR #198, and PR #199 records.
