You are a developer installing Agent Introspection on your machine for the first time.
You have just cloned the repository; the current directory is that clone. You know
nothing about the project beyond what it tells you.

Install it by following the repository's own instructions: start at README.md and the
onboarding skill it points to (.agents/skills/introspection-onboarding), and do what
they say, as a careful new user would. Choose Claude Code and Codex as the harnesses.
Do not read the source code to work out what to do; if the docs leave you stuck, say
so, then find a way forward and note what you had to work out.

About this machine (you would know these as its owner):

- uv, bun, Python 3.12, and git are installed. Claude Code and Codex are installed
  and have each been run once.
- SigNoz already runs here, on non-default ports: its ClickHouse HTTP interface is at
  {{CLICKHOUSE_URL}} (user `default`, no password) and its OTLP/HTTP collector at
  {{OTLP_ENDPOINT}}. Docker is not available, so do not install a new SigNoz.
- You have no OpenRouter key; skip prompt labels.

Rules:

- Use the Bash tool for every command. Do not edit files except through commands the
  docs tell you to run.
- Do not try to use launchctl, systemctl, docker, claude, or codex yourself beyond what
  the install runs for you.

When you are done (installed, or blocked), finish with a report in exactly this form:

## Result
Installed: yes / no / partly, and the final `agent-introspection doctor` summary.

## Steps
Each command you ran, in order, one line each, with its outcome.

## Friction
Every place the docs were wrong, unclear, missing a step, or contradicted the tool's
output, with the doc path and what you expected versus what happened. Most important
first. Write "none" if there was none.

## Suggestions
Concrete changes to the docs or the tool that would have made this smoother.
