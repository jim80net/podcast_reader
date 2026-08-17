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

## PR #208 — job and settings guidance

- Scope: failed-job disclosure and terminal progress copy on the New view, persistent
  New-source guidance, custom-provider validation presentation, and their unit/browser
  regressions.
- Reason: the published assets do not show the New form or a failed/completed job card.
  The published Settings frame uses the default provider, where the custom-provider URL
  field remains hidden, so its pixels and layout are unchanged. An installed refresh is
  host-blocked by the missing X display/Xvfb and Node v22.22.1 being below the Node 24
  floor tracked in issue #182.
- Review: independent review must verify the collapsed/expanded error state, terminal
  copy transition, Light/Dark 100%/125% source-guidance geometry, and custom-provider
  label/error association before merge.
- Expiry: this waiver applies only to PR #208 and expires when that PR merges. Issue #182
  remains the follow-up for restoring installed walkthrough capability.
