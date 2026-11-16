# Candidate rules

Rules an agent thinks this project should have. **Not binding.** John promotes a candidate into
CLAUDE.md, rewrites it, or deletes it. Append; don't edit others' entries.

Format:

```
## YYYY-MM-DD — <rule, one line>
Why: <what goes wrong without it>
From: <PR or issue>
```

## 2026-11-16 — A matching change adds the real case that prompted it to `tests/test_matching.py`
Why: the scorer's thresholds (brand required, 8-character codes, the all-words rule) were each
tuned against a real false positive; a change that isn't pinned by a case can quietly bring
one back, and nothing else in the suite would notice.
From: Phase 4 (agent docs), reading `matching.py` and `f25d789`

## 2026-11-16 — A resource cap is a constant, not a setting, until someone needs to tune it
Why: the feed and body caps were kept as constants (`docs/DECISIONS.md`, "Feed downloads and
request bodies are capped") because each variable is one more thing an installer can get wrong;
an agent adding a knob "for flexibility" undoes that.
From: Phase 4 (agent docs)
