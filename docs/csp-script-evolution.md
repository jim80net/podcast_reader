# Transcript CSP script evolution

Private-web transcripts run only byte-exact inline script sequences recognized by
`web_surface.py`. Treat a script edit as a policy migration, not a hash refresh.

## Evolve a script

1. Preserve the released text under its existing versioned constant. Derive the
   replacement from that constant with narrow `replace` operations when practical;
   this keeps the delta reviewable and makes accidental legacy drift visible.
2. Add the exact emitted text (including the renderer's leading newline) to
   `_TRANSCRIPT_SCRIPT_PINS` under a new stable name such as `search-v3`. Paste the
   digest reported by `uv run python scripts/csp_scripts.py list`; never compute it
   dynamically in the policy.
3. Add only complete renderer sequences to
   `_TRANSCRIPT_SCRIPT_SEQUENCE_NAMES`. Do not authorize individual scripts or
   construct a cross-product: every tuple is an explicit text-and-order contract.
4. Decide whether artifacts rendered by the preceding release still need to work.
   Keep their exact tuple while they are within the compatibility window. Record the
   decision in the PR. The #90 → #94 search repair deliberately retained
   `search-v1` beside `search-v2` because V1 artifacts had shipped the prior day.
5. Update `_EXPECTED_SCRIPT_SHAPES` for every new tuple. A recognized script
   sequence must still match the canonical renderer structure and controls.
6. Substitute the prior script into each renderer path in a behavior test and prove
   its preserved tuples still receive their exact hashes. Also test a mixed,
   reordered, repeated, or unknown-script tuple and require `script-src 'none'`.
7. Regenerate all renderer fixtures with `uv run python tests/regen_goldens.py`.
   Review changes under `tests/goldens/`, then run the gates below.

```bash
uv run python scripts/csp_scripts.py check
uv run pytest tests/engine/test_script_policy.py tests/engine/test_web_surface.py tests/test_html.py
uv run python tests/regen_goldens.py
git diff --exit-code -- tests/goldens
```

The checked policy fails closed. A digest mismatch, unknown tuple member, duplicate
text, repeated member, duplicate tuple, or orphaned pin makes the compiled allowlist
empty, so transcript responses authorize no inline script. CI runs the checker to
give the contributor the exact repair instead of discovering the failure in-browser.

The first post-playbook evolution is transcript export (#101): `export-v1` is a
new literal pin appended only to the three current `sync-v2` + `search-v2`
renderer tuples. The three pre-export current tuples remain byte-exact for stored
artifacts, while a `search-v1` + `export-v1` mix is a hostile rejection control.
This is the reference shape for adding a capability without blessing a tuple
cross-product.

## Retire a legacy sequence

Remove the old complete tuples first, with release-age evidence in the PR. Then
remove any now-unreferenced pin and, only when no standalone artifact or other reader
needs it, the historical renderer constant. The checker rejects orphaned pins so a
retirement cannot stop halfway. Keep committed historical fixtures when they are the
byte-level evidence for a supported compatibility boundary.
