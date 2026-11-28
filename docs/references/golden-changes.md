# Golden changes

`scripts/check-golden-changes.py` fails any change to a file in `proofs.toml` `[golden] globs` unless it adds a line here: the date, the file's path, and why it changed. Newest first.

- 2026-11-28: `docs/ux/feature-audit/titles.json` moved to `dashboard/src/feature-audit.titles.json`: moved beside its test; decisions moved in from the deleted feature-audit README.
