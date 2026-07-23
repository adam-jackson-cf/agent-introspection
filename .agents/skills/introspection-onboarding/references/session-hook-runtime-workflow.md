# Managed runtime installation workflow

## Objective

Install the skill-local session-context distribution at a stable versioned managed location.

## Required actions

1. Set `source_dir` to the directory containing this skill's `scripts/` directory.
2. Set `managed_dir` to `$HOME/.local/lib/agent-introspection/session-context-runtime-v1`.
3. Create `$managed_dir/adapters` and copy `session-context-runtime.sh` and `adapters/codex-app-server.sh` from `$source_dir/scripts/` into it.
4. Ensure the copied scripts are executable.
5. Configure every supported producer with only the copied adapter path under `$managed_dir`; never configure a hook to execute a skill source path.
6. Preserve the versioned directory name until all configured producers are deliberately migrated to another managed version.

## Portable commands

From the skill root, run:

```bash
source_dir=$(pwd)
managed_dir="$HOME/.local/lib/agent-introspection/session-context-runtime-v1"
mkdir -p "$managed_dir/adapters"
cp "$source_dir/scripts/session-context-runtime.sh" "$managed_dir/"
cp "$source_dir/scripts/adapters/codex-app-server.sh" "$managed_dir/adapters/"
chmod 755 "$managed_dir/session-context-runtime.sh" "$managed_dir/adapters/codex-app-server.sh"
```

## Done when

- The managed versioned directory contains executable runtime and adapter scripts, and hooks reference only that directory.
