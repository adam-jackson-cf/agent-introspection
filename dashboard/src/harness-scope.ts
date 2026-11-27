import {
  HARNESSES,
  type Harness,
  type HiddenSignal,
  type Registry,
} from "./contracts";

const REACHES = new Set(["aligned", "differs", "not applicable"]);

export const enabledHarnesses = (
  registry: Pick<Registry, "harnesses">,
): Harness[] =>
  HARNESSES.filter((harness) =>
    registry.harnesses.some(
      (entry) => entry.harness === harness && Number(entry.enabled) === 1,
    ),
  );

export function hiddenSignals(
  registry: Pick<Registry, "signals" | "support" | "harnesses">,
): HiddenSignal[] {
  const enabled = new Set<string>(enabledHarnesses(registry));
  const hidden: HiddenSignal[] = [];
  for (const signal of registry.signals) {
    if (signal.scope !== "harness") continue;
    const byNote = new Map<string, string[]>();
    for (const entry of registry.support)
      if (
        entry.signal === signal.signal &&
        enabled.has(entry.harness) &&
        !REACHES.has(entry.alignment)
      )
        byNote.set(entry.note, [
          ...(byNote.get(entry.note) ?? []),
          entry.harness_label,
        ]);
    for (const [note, labels] of byNote)
      hidden.push({
        signal: signal.signal,
        view: signal.view,
        title: signal.title,
        missing: labels.join(", "),
        note,
      });
  }
  return hidden;
}

export const isHidden = (
  registry: Pick<Registry, "hidden">,
  signal: string,
): boolean => registry.hidden.some((entry) => entry.signal === signal);
