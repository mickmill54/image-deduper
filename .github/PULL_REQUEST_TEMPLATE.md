<!--
Thanks for contributing! A few quick guidelines:

- Keep one logical change per PR. Split if you find yourself writing
  "and also" in the description.
- Reference the issue this closes (e.g. `Closes #42`) so it auto-closes
  on merge.
- All review-comment conversations must be resolved before merge.
- See CONTRIBUTING.md for branch model, commit conventions, and the
  required local checks.
-->

## Summary

<!-- One or two sentences on what this PR does and why. The "why" is
     usually more useful than the "what" -- the diff already shows the
     what. -->

## Closes

<!-- e.g. Closes #42. If this PR is exploratory and not closing
     anything, replace with "n/a". -->

## Test plan

<!-- Tick the boxes you've actually verified locally. CI runs the same
     checks on your PR; merging requires CI green. -->

- [ ] `make lint` passes (`ruff check src tests`)
- [ ] `make typecheck` passes (`pyright`, 0 errors)
- [ ] `make test` passes (full pytest suite)
- [ ] If behavior changed: docs updated in the same PR (README,
      `docs/architecture.md`, CHANGELOG)
- [ ] If a new failure mode is possible: tests cover it

## Notes for reviewers

<!-- Optional. Anything reviewers should know that isn't obvious from
     the diff -- design decisions, alternatives considered, follow-up
     work split into separate issues, etc. Delete this section if not
     applicable. -->
