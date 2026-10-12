import type { Registry, SignalDefinition } from "../contracts";
import { plain } from "../format";
import { groupOf, VIEWS } from "../routes";

/** Alignment as a class name: `not applicable` → `not-applicable`. */
const alignmentClass = (alignment: string) => alignment.replaceAll(" ", "-");

function SupportTable({
  registry,
  signal,
}: {
  registry: Registry;
  signal: string;
}) {
  const rows = registry.support.filter((entry) => entry.signal === signal);
  if (rows.length === 0) return null;
  return (
    <table>
      <thead>
        <tr>
          <th scope="col">Harness</th>
          <th scope="col">Alignment</th>
          <th scope="col">Note</th>
          <th scope="col">How it is captured</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((entry) => {
          const route = registry.routes.find(
            (candidate) =>
              candidate.route === entry.route &&
              candidate.harness === entry.harness,
          );
          return (
            <tr key={entry.harness}>
              <th scope="row">{entry.harness_label}</th>
              <td>
                <span
                  className={`alignment ${alignmentClass(entry.alignment)}`}
                >
                  {entry.alignment}
                </span>
              </td>
              <td>{plain(entry.note)}</td>
              <td>
                {entry.route && <code>{entry.route}</code>}
                {route && <small>{plain(route.description)}</small>}
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

function Entry({
  registry,
  definition,
}: {
  registry: Registry;
  definition: SignalDefinition;
}) {
  return (
    <article className="glossary-entry" id={definition.signal}>
      <h3>{definition.title}</h3>
      <p>{definition.question}</p>
      <p className="formula">{plain(definition.formula)}</p>
      {definition.scope === "harness" ? (
        <SupportTable registry={registry} signal={definition.signal} />
      ) : (
        <p className="formula">
          System-wide: it describes the pipeline, not a harness.
        </p>
      )}
    </article>
  );
}

/** Every signal's definition from the registry, grouped by view, with per-harness routes. */
export default function Glossary({ registry }: { registry: Registry }) {
  return (
    <>
      {VIEWS.map((view) => {
        const signals = registry.signals.filter(
          (entry) => entry.view === view.id,
        );
        if (signals.length === 0) return null;
        return (
          <section className="view-section" key={view.id}>
            <h2>
              {view.title} <small>{groupOf(view.id)}</small>
            </h2>
            {signals.map((definition) => (
              <Entry
                key={definition.signal}
                registry={registry}
                definition={definition}
              />
            ))}
          </section>
        );
      })}
      {registry.exclusions.length > 0 && (
        <section className="view-section">
          <h2>Not shown</h2>
          {registry.exclusions.map((entry) => (
            <article className="glossary-entry" key={entry.signal}>
              <h3>{entry.title}</h3>
              <p className="formula">
                No producer emits it ({entry.missing}). {plain(entry.reason)}
              </p>
            </article>
          ))}
        </section>
      )}
    </>
  );
}
