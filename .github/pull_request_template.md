## Summary

<!-- One or two sentences: what changes for the person using the app, and why. -->

Closes #

## What changes

-

## Verification

<!-- What was actually run and seen, not what should work. -->

- [ ] `uv run ruff check && uv run ruff format --check && uv run pytest`, or which ran in CI only
- [ ] `/code-review` run; findings fixed or answered below
- [ ] `/security-review` run (required if this touches the web UI's routes or guards, the Homebox token, write-back, notification URLs, or the container's boot)

## Docs

- [ ] CLAUDE.md / docs/ facts updated for anything moved, renamed or removed
- [ ] New rules proposed in `docs/claude-candidates.md`, not added to CLAUDE.md
