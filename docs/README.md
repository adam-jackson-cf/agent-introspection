# Documentation Index

Start at the root [README](../README.md) for install and first run.

## references/

- [golden-changes](references/golden-changes.md): the required record of why each golden test file changed (contributors).

## Other folders

- [features/](features/pipeline.md): the feature map, one page per dashboard view and per CLI feature: what it does, how to reach it, how the `sandbox.sh lever` drives it, its gotchas, and the tests that prove it; `scripts/check-verification-surface.py` checks each page (anyone checking a feature).
- [ux/](ux/AGENTS.md): design and UX standards; `ux/AGENTS.md` is the index.
- [todo/](todo/AGENTS.md): unscheduled feature work, one file per feature. Agents need the user's explicit permission to open it.
- [proofs.toml](../proofs.toml): the repository's rule contract (claims, enforcement points, canaries), read by `scripts/validate-rule-coverage.py`; not the dashboard's Guardrails page (contributors).
- [scripts/fresh-install/](../scripts/fresh-install/README.md): the fresh-install sandbox behind the gate's `install` mode (contributors).
