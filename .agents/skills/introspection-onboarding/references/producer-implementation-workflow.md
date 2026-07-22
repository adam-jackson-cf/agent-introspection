# Producer implementation workflow

## Objective

Apply the canonical project-attribution span contract at the producer emission point.

## Required actions

1. Load the canonical schema through the canonical-schema workflow before editing configuration or source.
2. Emit its complete project tuple together on every relevant session or action span.
3. Preserve the schema-required project-name and project-ID constraints.
4. Start a fresh producer process after configuration changes.
5. Do not use static process-level values for multiplexed or multi-workspace producers.
6. Do not derive values from CWD, paths, aliases, prompts, or thread inference.

## Done when

- The producer implementation emits the loaded contract at its real emission point.
