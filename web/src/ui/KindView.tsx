// A factory plugin's section on the project home (docs/plans/factory-plugins.md
// §4.9). The plugin declares which components it shows and fills them with
// data; this draws them. No plugin markup reaches the page: every value is
// text React escapes, and a link is either one of the project's own artifacts
// or an https URL. A section whose data did not fit its view never reaches here:
// the server sends the view's error instead, which is what is shown.

import { Link } from "react-router-dom";
import { KindView as View, KindViewSection } from "../api";
import { Badge } from "./kit";

const STATE_TONE = { ok: "good", info: "accent", warn: "warn", blocked: "critical" } as const;

function Section({ s, data, base }: { s: KindViewSection; data: any; base: string }) {
  if (data === undefined || data === null) return null;
  switch (s.component) {
    case "status":
      return <p><Badge tone={STATE_TONE[data.state as keyof typeof STATE_TONE]}>{data.state}</Badge>{" "}
        {data.text}</p>;
    case "metric":
      return <p className="kv-metric"><span className="kv-value">{String(data.value)}</span>
        {data.unit && <span className="help"> {data.unit}</span>}
        <span className="help"> · {data.label}</span>
        {data.note && <span className="help"> · {data.note}</span>}</p>;
    case "markdown":
      return <p style={{ whiteSpace: "pre-wrap" }}>{data.text}</p>;
    case "link":
      return data.href
        ? <p><a href={data.href} target="_blank" rel="noreferrer noopener">{data.label}</a></p>
        : <p><Link to={`${base}/deliverables`}>{data.label}</Link></p>;
    case "artifact":
      return <p><Link to={`${base}/deliverables`}>{data.name}</Link>
        {data.version ? <span className="help"> · v{data.version}</span> : null}
        {data.note && <span className="help"> · {data.note}</span>}</p>;
    case "checklist":
      return <ul className="kv-check">{(data.items ?? []).map((i: any, n: number) => (
        <li key={n}>{i.done ? "✓" : "○"} {i.text}</li>))}</ul>;
    case "coverage":
      return <ul className="kv-check">{(data.areas ?? []).map((a: any) => (
        <li key={a.key}><Badge tone={a.available ? "good" : undefined}>
          {a.available ? "reachable" : "not reachable"}</Badge> {a.key}
          {a.note && <span className="help"> · {a.note}</span>}</li>))}</ul>;
    case "table":
      return (
        <div className="scroller"><table className="kv-table">
          <thead><tr>{data.columns.map((c: string) => <th key={c}>{c}</th>)}</tr></thead>
          <tbody>{data.rows.map((r: unknown[], n: number) => (
            <tr key={n}>{r.map((v, m) => <td key={m}>{v === null ? "" : String(v)}</td>)}</tr>))}
          </tbody>
        </table></div>
      );
    default:
      return null;
  }
}

export function KindViewCard({ view, base }: { view: View; base: string }) {
  return (
    <section className="card kind-view" aria-label={view.label}>
      <h2 className="kv-head">{view.label}
        <span className="help"> · {view.plugin}@{view.version.slice(0, 12)}</span></h2>
      {view.error ? <p className="help">{view.error}</p>
        : view.doc?.sections.map((s) => (
          <div key={s.id} className="kv-section">
            <h3 className="kv-title" title={s.help}>{s.title}</h3>
            <Section s={s} data={view.data?.[s.id]} base={base} />
          </div>
        ))}
    </section>
  );
}
