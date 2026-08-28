import { useEffect, useMemo, useRef, useState } from "react";
import { get, getToken } from "../../api";
import Markdown from "../../Markdown";
import { fmtWhen, usePoll } from "../../hooks";
import { AsOf, Skeleton } from "../../ui";

interface WorkspaceFile {
  path: string;
  size: number;
  modified: string;
}

interface ArtifactFile {
  key: string;
  run_id: string | null;
  name: string;
  size: number;
  modified: string | null;
}

interface PublishedRow {
  id: string;
  name: string;
  version: number;
  sha256: string;
  size: number;
  key: string;
  published_at: string;
  published_by: string | null;
  satisfies: string[];
}

interface FilesView {
  workspace: WorkspaceFile[];
  artifacts: ArtifactFile[];
  // The receipts: platform-written rows with a real hash, a real publish
  // time, and the requirements each claims to satisfy.
  published?: PublishedRow[];
}

function fmtSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

async function fetchBytes(url: string): Promise<Blob> {
  const response = await fetch(url, {
    headers: { Authorization: `Bearer ${getToken()}` },
  });
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  return response.blob();
}

function download(blob: Blob, name: string) {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = name;
  anchor.click();
  URL.revokeObjectURL(url);
}

const PREVIEW_LIMIT = 2 * 1024 * 1024;

const EXT_LABEL: Record<string, string> = {
  html: "PAGE", htm: "PAGE", md: "DOC", txt: "DOC", csv: "DATA",
  json: "DATA", pdf: "PDF", png: "IMG", jpg: "IMG", jpeg: "IMG", svg: "IMG",
};

function extOf(name: string): string {
  return name.split(".").pop()?.toLowerCase() ?? "";
}

/** How to *show* a file, which is not the same question as what it is. A
    deliverable a chairman has to parse in his head is not a deliverable: a
    report is prose, a dataset is a table, and a config is indented. Text was
    the only answer here, so a published report arrived as its own asterisks. */
type Kind = "html" | "markdown" | "json" | "csv" | "text";

function kindOf(name: string): Kind {
  const ext = extOf(name);
  if (ext === "html" || ext === "htm") return "html";
  if (ext === "md" || ext === "markdown") return "markdown";
  if (ext === "json") return "json";
  if (ext === "csv" || ext === "tsv") return "csv";
  return "text";
}

/** The label on the toggle, in the words of the thing being toggled. "View
    source" is right for a page and wrong for a table. */
const SOURCE_LABEL: Record<Kind, [string, string]> = {
  html: ["view source", "show the page"],
  markdown: ["view source", "show the document"],
  json: ["view raw", "show it formatted"],
  csv: ["view raw", "show the table"],
  text: ["", ""],
};

/** RFC 4180, small enough to own: quoted fields, doubled quotes inside them,
    and delimiters or newlines that appear inside quotes and must not split
    anything. A split(",") reader gets every one of those wrong, and gets them
    wrong silently — the table renders, it just has the columns in the wrong
    places. */
function parseDelimited(text: string, delimiter: string): string[][] {
  const rows: string[][] = [];
  let row: string[] = [];
  let field = "";
  let quoted = false;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (quoted) {
      if (c !== '"') { field += c; continue; }
      // "" inside a quoted field is one literal quote, not the end of it.
      if (text[i + 1] === '"') { field += '"'; i++; } else quoted = false;
      continue;
    }
    if (c === '"') { quoted = true; continue; }
    if (c === delimiter) { row.push(field); field = ""; continue; }
    if (c === "\r") continue;
    if (c === "\n") { row.push(field); rows.push(row); row = []; field = ""; continue; }
    field += c;
  }
  if (field !== "" || row.length > 0) { row.push(field); rows.push(row); }
  return rows;
}

/** Half the "CSV" files in the world are semicolon- or tab-separated, and a
    guess from the extension alone renders those as one very wide column. Count
    what actually appears outside quotes in the first few lines and believe
    the file over its name. */
function sniffDelimiter(text: string, name: string): string {
  if (extOf(name) === "tsv") return "\t";
  const head = text.split("\n").slice(0, 5).join("\n");
  let quoted = false;
  const seen: Record<string, number> = { ",": 0, ";": 0, "\t": 0, "|": 0 };
  for (const c of head) {
    if (c === '"') { quoted = !quoted; continue; }
    if (!quoted && c in seen) seen[c] += 1;
  }
  const best = Object.entries(seen).sort((a, b) => b[1] - a[1])[0];
  return best[1] > 0 ? best[0] : ",";
}

// A browser will render a hundred thousand <td>s if asked, slowly, and then
// the tab stops answering. The rest of the file is one Download away, which is
// the honest thing to offer — as long as the cut is stated rather than silent.
const CSV_ROW_LIMIT = 500;

const NUMERIC = /^-?[$€£]?\s*\d[\d,_ ]*(\.\d+)?\s*%?$/;

function Raw({ text }: { text: string }) {
  return (
    <pre className="block" style={{ maxHeight: "60vh", overflow: "auto" }}>{text}</pre>
  );
}

/** Indented, not reformatted. Pretty-printing is the whole ask — an agent
    writes one 40KB line and the browser shows a 40KB line — but parsing is
    also the only way to find out the file is broken, and a chairman is better
    served by "this is not valid JSON" than by a wall that looks fine. */
function JsonBlock({ text }: { text: string }) {
  const pretty = useMemo(() => {
    try {
      return { text: JSON.stringify(JSON.parse(text), null, 2), ok: true };
    } catch (e) {
      return { text, ok: false, why: (e as Error).message };
    }
  }, [text]);
  return (
    <>
      {!pretty.ok && (
        <p className="help" style={{ margin: "0 0 8px" }}>
          Not valid JSON ({pretty.why}) — showing the file as written.
        </p>
      )}
      <Raw text={pretty.text} />
    </>
  );
}

/** A table, with the first row as the header — which is a guess, and the only
    one worth making: a spreadsheet export without a header row is rarer than a
    reader who cannot tell columns apart without one. */
function CsvTable({ text, name }: { text: string; name: string }) {
  const { head, body, total, width } = useMemo(() => {
    const rows = parseDelimited(text, sniffDelimiter(text, name))
      .filter((r) => r.length > 1 || r[0] !== "");
    const width = rows.reduce((w, r) => Math.max(w, r.length), 0);
    return {
      head: rows[0] ?? [],
      body: rows.slice(1, CSV_ROW_LIMIT + 1),
      total: Math.max(rows.length - 1, 0),
      width,
    };
  }, [text, name]);

  if (width === 0) return <Raw text={text} />;
  // Ragged rows are normal in exported data. Padding them here keeps every
  // cell under the header it belongs to; without it a short row silently
  // shifts every column after it left.
  const cells = (row: string[]) =>
    Array.from({ length: width }, (_, i) => row[i] ?? "");

  return (
    <>
      <div className="wbox" style={{ maxHeight: "60vh", overflow: "auto" }}>
        <table className="csv">
          <thead>
            <tr>
              {cells(head).map((h, i) => (
                <th key={i}>{h || <span className="dim">col {i + 1}</span>}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {body.map((row, r) => (
              <tr key={r}>
                {cells(row).map((cell, c) => (
                  <td key={c} className={NUMERIC.test(cell.trim()) ? "num mono" : ""}>
                    {cell}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="help" style={{ margin: "6px 0 0" }}>
        {total > body.length
          ? `First ${body.length} of ${total} rows · ${width} columns — Download for the whole file.`
          : `${total} row${total === 1 ? "" : "s"} · ${width} columns`}
      </p>
    </>
  );
}

/** One file, shown as the thing it is. `asSource` is the escape hatch and
    never the default: a chairman opening a report wants the report. */
function PreviewBody(
  { preview, asSource }: { preview: { name: string; text: string; kind: Kind }; asSource: boolean },
) {
  const { name, text, kind } = preview;
  if (asSource || kind === "text") return <Raw text={text} />;
  if (kind === "html") {
    // Agent-written HTML runs in an opaque origin: scripts work so the
    // charts render, but it can never reach this app's session.
    return (
      <iframe
        className="preview-frame"
        title={name}
        sandbox="allow-scripts"
        srcDoc={text}
      />
    );
  }
  if (kind === "markdown") {
    return (
      <div className="wbox doc" style={{ maxHeight: "60vh", overflow: "auto" }}>
        <Markdown>{text}</Markdown>
      </div>
    );
  }
  if (kind === "json") return <JsonBlock text={text} />;
  return <CsvTable text={text} name={name} />;
}

/** What the agents published, reframed around what it is rather than where it
    was stored. A deck a chairman cannot look at is not a deliverable, so HTML
    renders as itself; the source is a toggle, not the only option. */
export default function Deliverables({ projectId }: { projectId: string }) {
  const { data, error, at } = usePoll(
    () => get<FilesView>(`/observe/projects/${projectId}/files`),
    15000,
  );
  const [preview, setPreview] =
    useState<{ name: string; text: string; kind: Kind } | null>(null);
  const [asSource, setAsSource] = useState(false);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const [showWorking, setShowWorking] = useState(false);

  // The preview opens below a list that can be a screenful long. Without this,
  // pressing Open looks like pressing nothing.
  const previewRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (preview) previewRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  }, [preview]);

  async function open(url: string, name: string, size: number) {
    setPreviewError(null);
    try {
      const blob = await fetchBytes(url);
      if (size > PREVIEW_LIMIT) {
        download(blob, name);
        return;
      }
      const text = await blob.text();
      // A NUL byte means binary — hand it to the browser instead of the eye.
      if (text.slice(0, 8192).includes("\u0000")) {
        download(blob, name);
        return;
      }
      setAsSource(false);
      setPreview({ name, text, kind: kindOf(name) });
    } catch (e) {
      setPreviewError((e as Error).message);
    }
  }

  async function save(url: string, name: string) {
    setPreviewError(null);
    try {
      download(await fetchBytes(url), name);
    } catch (e) {
      setPreviewError((e as Error).message);
    }
  }

  const artifactUrl = (key: string) =>
    `/observe/projects/${projectId}/files/content?artifact=${encodeURIComponent(key)}`;

  const workspace = data?.workspace ?? [];
  const artifacts = data?.artifacts ?? [];
  const receiptByKey = new Map((data?.published ?? []).map((p) => [p.key, p]));

  // The same name republished by a later run is a new version of one thing.
  // Listing every run's copy as its own row made five files look like five
  // deliverables when there were three.
  const versions = new Map<string, ArtifactFile[]>();
  for (const a of artifacts) {
    versions.set(a.name, [...(versions.get(a.name) ?? []), a]);
  }
  const published = [...versions.entries()]
    .map(([name, all]) => {
      const sorted = [...all].sort((a, b) => (b.modified ?? "").localeCompare(a.modified ?? ""));
      return { name, latest: sorted[0], older: sorted.slice(1) };
    })
    .sort((a, b) => (b.latest.modified ?? "").localeCompare(a.latest.modified ?? ""));

  return (
    <>
      <p className="subtitle">
        What the client actually gets. Everything here was explicitly published by a team; the
        working files below are the agents' scratch pad, kept out of the way.
      </p>
      {error && !data && <div className="error">{error}</div>}
      {previewError && <div className="error">{previewError}</div>}

      <h2 className="zone">
        Published <span className="cnt">{published.length}</span>
      </h2>
      {!data ? (
        <Skeleton rows={3} />
      ) : published.length === 0 ? (
        <div className="wbox">
          <div className="wrow">
            <span className="badge">none yet</span>
            <div className="grow help">
              Nothing published. A deliverable appears the moment a team publishes one — drafts
              included, so you can read the work as it takes shape instead of waiting for the end.
            </div>
          </div>
        </div>
      ) : (
        <div className="wbox">
          {published.map(({ name, latest, older }) => (
            <div className="deliv" key={name}>
              <div className="icon">{EXT_LABEL[extOf(name)] ?? (extOf(name).toUpperCase() || "FILE")}</div>
              <div className="meta">
                <div className="pill-row" style={{ margin: 0 }}>
                  <span className="fname">{name}</span>
                  {older.length > 0 && <span className="chip">v{older.length + 1}</span>}
                </div>
                <div className="help" style={{ margin: "5px 0 9px" }}>
                  {fmtSize(latest.size)} ·{" "}
                  {latest.modified ? fmtWhen(latest.modified) : "—"}
                  {latest.run_id && ` · from run ${latest.run_id.slice(-8)}`}
                  {(() => {
                    const receipt = receiptByKey.get(latest.key);
                    if (!receipt) return null;
                    return (
                      <>
                        {receipt.satisfies.length > 0 &&
                          ` · satisfies ${receipt.satisfies.join(", ")}`}
                        {" · "}
                        <span title={`sha256 ${receipt.sha256}`} className="mono">
                          sha {receipt.sha256.slice(0, 10)}…
                        </span>
                      </>
                    );
                  })()}
                </div>
                <div className="pill-row" style={{ margin: 0 }}>
                  <button className="small primary" onClick={() => open(artifactUrl(latest.key), name, latest.size)}>
                    Open
                  </button>
                  <button className="small" onClick={() => save(artifactUrl(latest.key), name)}>
                    Download
                  </button>
                  {older.length > 0 && (
                    <button
                      className="small"
                      onClick={() => setExpanded((e) => ({ ...e, [name]: !e[name] }))}
                    >
                      {expanded[name]
                        ? "hide earlier versions"
                        : `${older.length} earlier version${older.length === 1 ? "" : "s"}`}
                    </button>
                  )}
                </div>
                {expanded[name] && (
                  <table style={{ marginTop: 10 }}>
                    <thead>
                      <tr>
                        <th>Published</th>
                        <th>From run</th>
                        <th className="num">Size</th>
                        <th></th>
                      </tr>
                    </thead>
                    <tbody>
                      {older.map((o) => (
                        <tr key={o.key}>
                          <td className="mono">{o.modified ? fmtWhen(o.modified) : "—"}</td>
                          <td className="mono">{o.run_id ? o.run_id.slice(-8) : "—"}</td>
                          <td className="num">{fmtSize(o.size)}</td>
                          <td>
                            <button className="small" onClick={() => open(artifactUrl(o.key), name, o.size)}>
                              open
                            </button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </div>
            </div>
          ))}
        </div>
      )}

      {preview && (
        <div ref={previewRef} style={{ scrollMarginTop: 12 }}>
          <h2 className="zone">
            {preview.name}
            <div className="spacer" />
            {preview.kind !== "text" && (
              <button className="small" onClick={() => setAsSource(!asSource)}>
                {SOURCE_LABEL[preview.kind][asSource ? 1 : 0]}
              </button>
            )}
            <button className="small" onClick={() => setPreview(null)}>close</button>
          </h2>
          <PreviewBody preview={preview} asSource={asSource} />
        </div>
      )}

      <h2 className="zone">
        Working files <span className="cnt">{workspace.length}</span>
      </h2>
      <p className="help" style={{ margin: "0 0 10px" }}>
        The agents' shared scratch pad — drafts and intermediates, changing as they work. Nothing
        here is finished.
      </p>
      {workspace.length === 0 ? (
        <p className="help">Nothing yet — agents write here once they start working.</p>
      ) : !showWorking ? (
        <button className="small" onClick={() => setShowWorking(true)}>
          show {workspace.length} working file{workspace.length === 1 ? "" : "s"}
        </button>
      ) : (
        <div className="wbox">
          <table>
            <thead>
              <tr>
                <th style={{ width: "50%" }}>File</th>
                <th className="num">Size</th>
                <th>Changed</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {workspace.map((file) => (
                <tr key={file.path}>
                  <td className="mono">{file.path}</td>
                  <td className="num">{fmtSize(file.size)}</td>
                  <td className="mono">{fmtWhen(file.modified)}</td>
                  <td>
                    <button
                      className="small"
                      onClick={() =>
                        open(
                          `/observe/projects/${projectId}/files/content?path=${encodeURIComponent(file.path)}`,
                          file.path.split("/").pop() ?? file.path,
                          file.size,
                        )
                      }
                    >
                      open
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <AsOf at={at} error={error} />
    </>
  );
}
