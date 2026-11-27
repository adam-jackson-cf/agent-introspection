# Golden changes

A golden file is a stored file a test loads as its expected output. A change to one
changes what the tests accept, so each change needs an entry here that names the file
and says why it changed. `proofs.toml` `[golden]` lists the golden files, and
`bash scripts/run-ci-quality-gates.sh check golden_changes` fails a change that
edits one without adding an entry naming it.

| Golden file                         | What it holds                                                                |
| ----------------------------------- | ---------------------------------------------------------------------------- |
| `tests/fixtures/command_shape.json` | The command-shape cases the Python and SQL command shapes must both produce. |
| `docs/ux/feature-audit/titles.json` | The dashboard titles the feature-audit test compares the views with.         |

## Entries

Add one line per change, newest first: the date, the golden file's path, and why it
changed.
