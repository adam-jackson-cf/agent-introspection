export const HARNESSES = [
  "oh-my-pi",
  "codex-app-server",
  "codex_cli_rs",
  "codex_exec",
  "claude-code",
] as const;
export type Harness = (typeof HARNESSES)[number];

export const HARNESS_LABEL: Record<Harness, string> = {
  "oh-my-pi": "omp",
  "codex-app-server": "Codex app-server",
  codex_cli_rs: "Codex CLI",
  codex_exec: "Codex exec",
  "claude-code": "Claude Code",
};

export const HARNESS_SERVICES_SQL =
  "'oh-my-pi', 'codex-app-server', 'codex_cli_rs', 'codex_exec', 'claude-code'";
