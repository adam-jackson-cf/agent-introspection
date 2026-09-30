import { DataTable } from "../charts";
import type { SessionResponse } from "../contracts";
import { HarnessName, Section } from "../components";
import { fmtCount, fmtSeconds, maybe, num } from "../format";

const flag = (value: unknown) =>
  value === null || value === undefined ? "—" : num(value) === 1 ? "yes" : "no";

export default function Session({ response }: { response: SessionResponse }) {
  const { data } = response;
  const tasks = data.tasks ?? [];
  const calls = data.calls ?? [];
  const failed = calls.filter((row) => row.outcome === "failed").length;
  return (
    <>
      <div className="session-head">
        <HarnessName value={response.harness} />
        <code>{response.session}</code>
        <span>
          {tasks.length} tasks · {calls.length} tool calls · {failed} failed
        </span>
      </div>
      <Section title="Tasks">
        <article className="panel" data-span={12}>
          <div className="panel-body">
            <DataTable
              columns={[
                {
                  key: "started",
                  label: "Started (UTC)",
                  render: (row) => String(row.started).slice(0, 19),
                },
                { key: "model", label: "Model" },
                { key: "effort", label: "Effort" },
                {
                  key: "duration_seconds",
                  label: "Duration",
                  numeric: true,
                  render: (row) => fmtSeconds(maybe(row.duration_seconds)),
                },
                { key: "model_steps", label: "Steps", numeric: true },
                {
                  key: "reasoning_tokens",
                  label: "Reasoning",
                  numeric: true,
                  render: (row) => fmtCount(maybe(row.reasoning_tokens)),
                },
                {
                  key: "interrupted",
                  label: "Interrupted",
                  render: (row) => flag(row.interrupted),
                },
                {
                  key: "steered",
                  label: "Steered",
                  render: (row) => flag(row.steered),
                },
                {
                  key: "errored",
                  label: "Errored",
                  render: (row) => flag(row.errored),
                },
                {
                  key: "quick_follow_up",
                  label: "Follow-up",
                  render: (row) => flag(row.quick_follow_up),
                },
                {
                  key: "clean_completion",
                  label: "Clean",
                  render: (row) => flag(row.clean_completion),
                },
                { key: "observed", label: "Friction observed" },
              ]}
              rows={tasks}
              empty="No tasks in this session (usage without a task boundary, or a slash command)."
            />
          </div>
        </article>
      </Section>
      <Section title="Model usage and calls">
        <article className="panel" data-span={7}>
          <div className="panel-body">
            <DataTable
              columns={[
                { key: "model", label: "Model" },
                { key: "effort", label: "Effort" },
                { key: "operations", label: "Operations", numeric: true },
                {
                  key: "input",
                  label: "Input",
                  numeric: true,
                  render: (row) => fmtCount(num(row.input)),
                },
                {
                  key: "cached",
                  label: "Cached",
                  numeric: true,
                  render: (row) => fmtCount(num(row.cached)),
                },
                {
                  key: "output",
                  label: "Output",
                  numeric: true,
                  render: (row) => fmtCount(num(row.output)),
                },
                {
                  key: "reasoning",
                  label: "Reasoning",
                  numeric: true,
                  render: (row) => fmtCount(maybe(row.reasoning)),
                },
              ]}
              rows={data.usage ?? []}
            />
          </div>
        </article>
        <article className="panel" data-span={5}>
          <div className="panel-body">
            <DataTable
              columns={[
                { key: "outcome", label: "Outcome" },
                {
                  key: "error_class",
                  label: "Error",
                  render: (row) => String(row.error_class) || "—",
                },
                { key: "n", label: "Calls", numeric: true },
                {
                  key: "ttft_p50",
                  label: "TTFT p50",
                  numeric: true,
                  render: (row) => fmtSeconds(maybe(row.ttft_p50)),
                },
              ]}
              rows={data.model_calls ?? []}
            />
          </div>
        </article>
      </Section>
      <Section title="Tool calls">
        <article className="panel" data-span={12}>
          <div className="panel-body">
            <DataTable
              columns={[
                {
                  key: "at",
                  label: "At (UTC)",
                  render: (row) => String(row.at).slice(11, 19),
                },
                { key: "tool", label: "Tool" },
                { key: "outcome", label: "Outcome" },
                {
                  key: "command_head",
                  label: "Command",
                  render: (row) =>
                    `${String(row.command_head)} ${String(row.command_sub)}`.trim() ||
                    "—",
                },
                { key: "exit_code", label: "Exit", numeric: true },
                {
                  key: "failure_signature",
                  label: "Failure signature",
                  render: (row) => String(row.failure_signature) || "—",
                },
                {
                  key: "targets",
                  label: "Targets",
                  render: (row) => String(row.targets) || "—",
                },
              ]}
              rows={calls}
            />
          </div>
        </article>
      </Section>
      <Section title="User and policy signals">
        <article className="panel" data-span={12}>
          <div className="panel-body">
            <DataTable
              columns={[
                {
                  key: "at",
                  label: "At (UTC)",
                  render: (row) => String(row.at).slice(0, 19),
                },
                { key: "signal", label: "Signal" },
                { key: "detail", label: "Detail" },
                { key: "source", label: "Source" },
                { key: "tool", label: "Tool" },
              ]}
              rows={data.signals ?? []}
              empty="No interrupts, steers, approvals, or sandbox outcomes in this session."
            />
          </div>
        </article>
      </Section>
    </>
  );
}
