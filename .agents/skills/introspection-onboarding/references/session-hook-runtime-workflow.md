# Managed runtime installation workflow

## Objective

Install the skill-local session-context distribution at a stable versioned managed location.

## Guidance

- Set source_dir to the directory containing this skill's scripts directory.
- Set managed_dir to the current versioned path under $HOME/.local/lib/agent-introspection.
- Create the managed adapter directory and copy only the runtime and producer adapters required by the approved configuration.
- Ensure copied scripts are executable.
- Configure supported producers with only the copied managed adapter path; never execute a skill source path from a hook.
- Preserve the versioned directory until every configured producer is deliberately migrated to another managed version.
- Validate installed file presence and executable mode without printing file content that could contain local configuration.
- Complete this workflow when the managed versioned directory contains the required executable runtime and adapters and every hook references only that directory.
