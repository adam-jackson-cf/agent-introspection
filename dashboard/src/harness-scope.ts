import {
  HARNESSES,
  type Harness,
  type HiddenSignal,
  type Registry,
} from "./contracts";

/** Alignments that reach a signal on a machine using the harness. */
const REACHES = new Set(["aligned", "differs", "not applicable"]);

/** The harnesses this machine uses, in registry order. */
export const enabledHarnesses = (
  registry: Pick<Registry, "harnesses">,
): Harness[] =>
  HARNESSES.filter((harness) =>
    registry.harnesses.some(
      (entry) => entry.harness === harness && Number(entry.enabled) === 1,
    ),
  );

/**
 * Harness-scoped signals some enabled harness does not emit, one entry per signal
 * and reason. Parity is judged over the enabled harnesses only, so a signal is
 * shown when every enabled harness reaches it and hidden otherwise.
 */
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

/** Whether the signal is hidden on this machine. */
export const isHidden = (
  registry: Pick<Registry, "hidden">,
  signal: string,
): boolean => registry.hidden.some((entry) => entry.signal === signal);
