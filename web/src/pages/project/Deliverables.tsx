import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { ApiError, get } from "../../api";
import Markdown from "../../Markdown";
import { fmtWhen, usePoll } from "../../hooks";
import { AsOf, ExportCsv, Skeleton, downloadCsv } from "../../ui";

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

/** One row as the index sends it. The server does the searching, sorting and
    foldering now — a project with fifty thousand files cannot be shipped to a
    browser every fifteen seconds and sorted there — so a row arrives already
    knowing its place (`pos`) in the filtered list. */
interface FileRow {
  id: string;
  name: string;
  dir: string;
  state: State;
  ext: string;
  size: number;
  at: string | null;
  path: string | null;
  key: string | null;
  run_id: string | null;
  version: number;
  pos: number | null;
  // The receipt: a platform-written row with a real hash, a real publish
  // time, and the requirements it claims to satisfy.
  receipt: PublishedRow | null;
  older: FileRow[];
}

interface DayFolder {
  key: string;
  count: number;
  published: number;
  open: boolean;
  cap: number;
  /** Null while the folder is closed: its rows were never sent. */
  rows: FileRow[] | null;
}

interface BrowseView {
  as_of: string;
  total: number;
  published_total: number;
  today_total: number;
  matched: number;
  states: Record<"all" | "published" | "working", number>;
  types: [string, number][];
  days: DayFolder[];
  // How many working files the index had to leave out to hold the rest. A
  // listing that silently stops at its cap is a listing that lies about the
  // project.
  working_kept: number;
  working_dropped: number;
  walk_stopped: boolean;
}

interface RowsView {
  matched: number;
  rows: FileRow[];
}

function fmtSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function fmtCount(n: number): string {
  return n.toLocaleString();
}

async function fetchBytes(url: string): Promise<Blob> {
  // The session cookie authenticates this like any other request; the bytes
  // are why it is a raw fetch rather than `get`, which parses JSON.
  const response = await fetch(url, { credentials: "same-origin" });
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

// ── what a file is, which is not the same question as where it was stored ────

/** How to *show* a file. A deliverable a chairman has to parse in his head is
    not a deliverable: a report is prose, a dataset is a table, a chart is a
    picture and a config is indented. */
type Kind =
  | "html" | "markdown" | "json" | "jsonl" | "csv"
  | "image" | "pdf" | "text" | "binary";

const EXT_KIND: Record<string, Kind> = {
  html: "html", htm: "html", xhtml: "html",
  md: "markdown", markdown: "markdown", mdx: "markdown",
  json: "json", geojson: "json", ipynb: "json",
  jsonl: "jsonl", ndjson: "jsonl",
  csv: "csv", tsv: "csv",
  png: "image", jpg: "image", jpeg: "image", gif: "image", webp: "image",
  svg: "image", bmp: "image", ico: "image", avif: "image",
  pdf: "pdf",
  zip: "binary", gz: "binary", tgz: "binary", tar: "binary", xz: "binary",
  "7z": "binary", rar: "binary", xlsx: "binary", xls: "binary", docx: "binary",
  doc: "binary", pptx: "binary", ppt: "binary", sqlite: "binary", db: "binary",
  bin: "binary", exe: "binary", dll: "binary", so: "binary", dylib: "binary",
  woff: "binary", woff2: "binary", ttf: "binary", otf: "binary", mp3: "binary",
  mp4: "binary", mov: "binary", wav: "binary", ogg: "binary", webm: "binary",
  parquet: "binary", pkl: "binary", npy: "binary",
};

/** The browser has to be told what the bytes are before it will draw them —
    the endpoint hands everything over as octet-stream on purpose. */
const IMAGE_MIME: Record<string, string> = {
  png: "image/png", jpg: "image/jpeg", jpeg: "image/jpeg", gif: "image/gif",
  webp: "image/webp", svg: "image/svg+xml", bmp: "image/bmp",
  ico: "image/x-icon", avif: "image/avif",
};

/** Colour family for the type badge. Six families, because a reader scanning a
    thousand rows is looking for "the report" and "the data", not for ".mdx". */
const FAMILY: Record<Kind, string> = {
  html: "page", markdown: "doc", json: "data", jsonl: "data", csv: "data",
  image: "img", pdf: "pdf", text: "txt", binary: "bin",
};

function extOf(name: string): string {
  const base = name.split("/").pop() ?? name;
  if (!base.includes(".")) return "";
  return base.split(".").pop()!.toLowerCase();
}

function kindOf(name: string): Kind {
  return EXT_KIND[extOf(name)] ?? "text";
}

/** The label on the source toggle, in the words of the thing being toggled.
    "View source" is right for a page and wrong for a table. */
const SOURCE_LABEL: Partial<Record<Kind, [string, string]>> = {
  html: ["view source", "show the page"],
  markdown: ["view source", "show the document"],
  json: ["view raw", "show the tree"],
  jsonl: ["view raw", "show the records"],
  csv: ["view raw", "show the table"],
};

// ── the rows ────────────────────────────────────────────────────────────────

type State = "published" | "working";

interface Entry {
  id: string;
  /** What to call it: the file's own name, never the storage key. */
  name: string;
  /** Where it sits, for working files. Published files have no folder — the
      store's run-keyed path is plumbing, not a place the chairman filed it. */
  dir: string;
  size: number;
  at: string | null;
  state: State;
  ext: string;
  kind: Kind;
  url: string;
  key: string | null;
  runId: string | null;
  version: number;
  receipt: PublishedRow | null;
  /** Earlier publications of the same name, newest first. */
  older: Entry[];
  /** Where this row sits in the filtered list, which is what the reader walks.
      Null for an earlier version, which is not in that list. */
  pos: number | null;
}

function toEntry(row: FileRow, projectId: string): Entry {
  const content = `/observe/projects/${projectId}/files/content`;
  return {
    id: row.id,
    name: row.name,
    dir: row.dir,
    size: row.size,
    at: row.at,
    state: row.state,
    ext: row.ext,
    kind: kindOf(row.name),
    url: row.key !== null
      ? `${content}?artifact=${encodeURIComponent(row.key)}`
      : `${content}?path=${encodeURIComponent(row.path ?? "")}`,
    key: row.key,
    runId: row.run_id,
    version: row.version,
    receipt: row.receipt,
    older: (row.older ?? []).map((o) => toEntry(o, projectId)),
    pos: row.pos,
  };
}

// ── dates: the organising idea of the whole page ────────────────────────────

const TIME = new Intl.DateTimeFormat(undefined, { hour: "2-digit", minute: "2-digit" });
const DAY_NEAR = new Intl.DateTimeFormat(undefined, {
  weekday: "long", month: "short", day: "numeric",
});
const DAY_FAR = new Intl.DateTimeFormat(undefined, {
  month: "short", day: "numeric", year: "numeric",
});
const STAMP = new Intl.DateTimeFormat(undefined, {
  year: "numeric", month: "short", day: "numeric",
  hour: "2-digit", minute: "2-digit", second: "2-digit",
});

/** Local days, not UTC ones. A file written at 6pm in California belongs in
    *this evening's* folder, and an ISO-prefix comparison would file it under
    tomorrow. The server folders by the zone named here, so its day keys and
    the labels below agree about when midnight was. */
const TZ = (() => {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  } catch {
    return "UTC";
  }
})();

function dayOf(d: Date): string {
  const m = `${d.getMonth() + 1}`.padStart(2, "0");
  const day = `${d.getDate()}`.padStart(2, "0");
  return `${d.getFullYear()}-${m}-${day}`;
}

function startOfDay(offsetDays = 0): Date {
  const d = new Date();
  d.setHours(0, 0, 0, 0);
  d.setDate(d.getDate() - offsetDays);
  return d;
}

function dayLabel(key: string): string {
  if (key === "unknown") return "No date";
  const today = dayOf(startOfDay());
  const yesterday = dayOf(startOfDay(1));
  if (key === today) return "Today";
  if (key === yesterday) return "Yesterday";
  const when = new Date(`${key}T12:00:00`);
  const age = (Date.now() - when.getTime()) / 86_400_000;
  return age < 7 ? DAY_NEAR.format(when) : DAY_FAR.format(when);
}

// ── previews ────────────────────────────────────────────────────────────────

// Decoded, not downloaded. Past this the file is still previewable — the head
// of it is — and saying so beats the old behaviour, which was to silently
// start a download when you pressed Open.
const TEXT_LIMIT = 2 * 1024 * 1024;
const LINE_LIMIT = 5000;

/** Text as text: a gutter that scrolls with the body, so a stack trace or a
    log line can be pointed at by number. Wrapping turns the numbers into
    lies — a wrapped line takes three rows and the gutter counts one — so the
    gutter leaves when wrap arrives. */
function CodeBlock({ text, wrap }: { text: string; wrap: boolean }) {
  const { body, clipped, lines } = useMemo(() => {
    const all = text.split("\n");
    return {
      body: all.length > LINE_LIMIT ? all.slice(0, LINE_LIMIT).join("\n") : text,
      clipped: Math.max(all.length - LINE_LIMIT, 0),
      lines: Math.min(all.length, LINE_LIMIT),
    };
  }, [text]);
  const gutter = useMemo(
    () => Array.from({ length: lines }, (_, i) => i + 1).join("\n"),
    [lines],
  );
  return (
    <>
      <div className={`code${wrap ? " wrap" : ""}`}>
        {!wrap && <pre className="gutter" aria-hidden="true">{gutter}</pre>}
        <pre className="codebody">{body}</pre>
      </div>
      {clipped > 0 && (
        <p className="help" style={{ margin: "6px 0 0" }}>
          First {fmtCount(LINE_LIMIT)} lines · {fmtCount(clipped)} more — Download for the
          whole file.
        </p>
      )}
    </>
  );
}

const JSON_CHILD_LIMIT = 300;

/** A tree, because an agent's JSON is nested and forty screens long, and the
    question asked of it is nearly always "what is in this one branch". Opens
    two levels deep: enough to see the shape, not enough to hide it. */
function JsonNode({ label, value, depth }: { label?: string; value: unknown; depth: number }) {
  const [open, setOpen] = useState(depth < 2);
  const [all, setAll] = useState(false);

  if (value === null) return <Leaf label={label} cls="j-null" text="null" />;
  if (typeof value === "string") return <Leaf label={label} cls="j-str" text={`"${value}"`} />;
  if (typeof value === "number") return <Leaf label={label} cls="j-num" text={String(value)} />;
  if (typeof value === "boolean") return <Leaf label={label} cls="j-bool" text={String(value)} />;

  const isArray = Array.isArray(value);
  const pairs: [string, unknown][] = isArray
    ? (value as unknown[]).map((v, i) => [String(i), v])
    : Object.entries(value as Record<string, unknown>);
  const shown = all ? pairs : pairs.slice(0, JSON_CHILD_LIMIT);
  const summary = isArray
    ? `[${pairs.length}]`
    : `{${pairs.length} ${pairs.length === 1 ? "key" : "keys"}}`;

  return (
    <div className="j-node">
      <button className="j-tog" aria-expanded={open} onClick={() => setOpen(!open)}>
        <span className={`caret${open ? " open" : ""}`} aria-hidden="true">▸</span>
        {label !== undefined && <span className="j-key">{label}</span>}
        <span className="j-sum">{summary}</span>
        {!open && <span className="j-peek">{peek(pairs, isArray)}</span>}
      </button>
      {open && (
        <div className="j-kids">
          {shown.map(([k, v]) => (
            <JsonNode key={k} label={k} value={v} depth={depth + 1} />
          ))}
          {shown.length < pairs.length && (
            <button className="linkish" onClick={() => setAll(true)}>
              show {fmtCount(pairs.length - shown.length)} more
            </button>
          )}
        </div>
      )}
    </div>
  );
}

/** What a closed branch is holding, in one line. Without it a log of a
    thousand records collapses to a thousand identical "{4 keys}" and the
    reader has to open every one to find the failure. */
function peek(pairs: [string, unknown][], isArray: boolean): string {
  const bits = pairs.slice(0, 4).map(([k, v]) => {
    const value = v === null ? "null"
      : typeof v === "object" ? (Array.isArray(v) ? "[…]" : "{…}")
      : typeof v === "string" ? `"${v}"`
      : String(v);
    return isArray ? value : `${k}: ${value}`;
  });
  const line = bits.join(", ");
  const clipped = line.length > 76 ? `${line.slice(0, 76)}…` : line;
  return pairs.length > bits.length ? `${clipped}, …` : clipped;
}

function Leaf({ label, cls, text }: { label?: string; cls: string; text: string }) {
  return (
    <div className="j-leaf">
      {label !== undefined && <span className="j-key">{label}</span>}
      <span className={cls}>{text}</span>
    </div>
  );
}

/** Parsing is also the only way to find out the file is broken, and a reader is
    better served by "this is not valid JSON, here is why" than by a wall that
    looks fine. */
function JsonBlock({ text, wrap }: { text: string; wrap: boolean }) {
  const parsed = useMemo(() => {
    try {
      return { value: JSON.parse(text) as unknown, ok: true as const };
    } catch (e) {
      return { ok: false as const, why: (e as Error).message };
    }
  }, [text]);
  if (!parsed.ok) {
    return (
      <>
        <p className="help" style={{ margin: "0 0 8px" }}>
          Not valid JSON ({parsed.why}) — showing the file as written.
        </p>
        <CodeBlock text={text} wrap={wrap} />
      </>
    );
  }
  return (
    <div className="jsontree">
      <JsonNode value={parsed.value} depth={0} />
    </div>
  );
}

/** One JSON object per line — how every agent-written event log is shaped.
    Pretty-printing the file as a whole fails on the second line; reading it as
    records is the only shape that works. */
function JsonLines({ text, wrap }: { text: string; wrap: boolean }) {
  const rows = useMemo(
    () => text.split("\n").map((l) => l.trim()).filter(Boolean).slice(0, 1000),
    [text],
  );
  const total = useMemo(() => text.split("\n").filter((l) => l.trim()).length, [text]);
  return (
    <>
      <div className="jsontree">
        {rows.map((line, i) => {
          let value: unknown;
          try {
            value = JSON.parse(line);
          } catch {
            return <Leaf key={i} label={String(i + 1)} cls="j-str" text={line} />;
          }
          return <JsonNode key={i} label={String(i + 1)} value={value} depth={2} />;
        })}
      </div>
      {total > rows.length && (
        <p className="help" style={{ margin: "6px 0 0" }}>
          First {fmtCount(rows.length)} of {fmtCount(total)} records — Download for the whole
          file.
        </p>
      )}
      {rows.length === 0 && <CodeBlock text={text} wrap={wrap} />}
    </>
  );
}

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
// the tab stops answering. Another page of rows is one press away, and the
// rest of the file is one Download away — as long as the cut is stated.
const CSV_PAGE = 500;

const NUMERIC = /^-?[$€£]?\s*\d[\d,_ ]*(\.\d+)?\s*%?$/;

/** A table, with the first row as the header — which is a guess, and the only
    one worth making: a spreadsheet export without a header row is rarer than a
    reader who cannot tell columns apart without one. */
function CsvTable({ text, name }: { text: string; name: string }) {
  const [limit, setLimit] = useState(CSV_PAGE);
  const { head, rows, width, numeric } = useMemo(() => {
    const parsed = parseDelimited(text, sniffDelimiter(text, name))
      .filter((r) => r.length > 1 || r[0] !== "");
    const body = parsed.slice(1);
    const width = parsed.reduce((w, r) => Math.max(w, r.length), 0);
    // A column is a number column when its values are, which is a question
    // about the column and not about the cell: judging each cell alone puts a
    // blank or a "n/a" back on the left and the column stops lining up.
    const numeric = Array.from({ length: width }, (_, c) => {
      let seen = 0;
      let hits = 0;
      for (const row of body.slice(0, 50)) {
        const value = (row[c] ?? "").trim();
        if (!value) continue;
        seen += 1;
        if (NUMERIC.test(value)) hits += 1;
      }
      return seen > 0 && hits / seen >= 0.8;
    });
    return { head: parsed[0] ?? [], rows: body, width, numeric };
  }, [text, name]);

  if (width === 0) return <CodeBlock text={text} wrap={false} />;
  const body = rows.slice(0, limit);
  // Ragged rows are normal in exported data. Padding them here keeps every
  // cell under the header it belongs to; without it a short row silently
  // shifts every column after it left.
  const cells = (row: string[]) =>
    Array.from({ length: width }, (_, i) => row[i] ?? "");

  return (
    <>
      <div className="wbox scroller">
        <table className="csv">
          <thead>
            <tr>
              <th className="rownum"></th>
              {cells(head).map((h, i) => (
                <th key={i} className={numeric[i] ? "num" : ""}>
                  {h || <span className="dim">col {i + 1}</span>}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {body.map((row, r) => (
              <tr key={r}>
                <td className="rownum">{r + 1}</td>
                {cells(row).map((cell, c) => (
                  <td key={c} className={numeric[c] ? "num" : ""}>{cell}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="help" style={{ margin: "6px 0 0" }}>
        {rows.length > body.length ? (
          <>
            First {fmtCount(body.length)} of {fmtCount(rows.length)} rows · {width} columns{" "}
            <button className="linkish" onClick={() => setLimit(limit + CSV_PAGE * 4)}>
              show more
            </button>
          </>
        ) : (
          `${fmtCount(rows.length)} row${rows.length === 1 ? "" : "s"} · ${width} columns`
        )}
      </p>
    </>
  );
}

/** What a fetched file turned out to be, once its bytes were in hand. The
    extension proposes; the first 8KB decides. */
interface Loaded {
  entry: Entry;
  kind: Kind;
  blob: Blob;
  text?: string;
  /** Object URL for the kinds the browser draws itself. */
  objectUrl?: string;
  shownBytes?: number;
}

function PreviewBody(
  { loaded, asSource, wrap }: { loaded: Loaded; asSource: boolean; wrap: boolean },
) {
  const { entry, kind, text, objectUrl } = loaded;

  if (kind === "image") {
    // An <img> decodes; it does not execute. That is what makes it safe to
    // show an agent-written .svg here at all, on the origin that holds the
    // chairman's token — the same file in an <iframe> would run as us.
    return <ImageView url={objectUrl} name={entry.name} />;
  }
  if (kind === "pdf") {
    // The bytes go to the browser's own PDF viewer, which is a separate
    // renderer with no reach into this document.
    return (
      <object className="pdfview" data={objectUrl} type="application/pdf" aria-label={entry.name}>
        <p className="help">
          This browser will not display PDFs inline — Download to read it.
        </p>
      </object>
    );
  }
  if (kind === "binary" || text === undefined) {
    return (
      <div className="wbox" style={{ padding: 16 }}>
        <p className="help" style={{ margin: 0 }}>
          {fmtSize(entry.size)} of binary — there is nothing to read on a screen.
          Download opens it in whatever wrote it.
        </p>
      </div>
    );
  }
  if (asSource || kind === "text") return <CodeBlock text={text} wrap={wrap} />;
  if (kind === "html") {
    // Agent-written HTML runs in an opaque origin: scripts work so the
    // charts render, but it can never reach this app's session.
    return (
      <iframe
        className="preview-frame"
        title={entry.name}
        sandbox="allow-scripts"
        srcDoc={text}
      />
    );
  }
  if (kind === "markdown") {
    return (
      <div className="wbox doc scroller">
        <Markdown>{text}</Markdown>
      </div>
    );
  }
  if (kind === "json") return <JsonBlock text={text} wrap={wrap} />;
  if (kind === "jsonl") return <JsonLines text={text} wrap={wrap} />;
  return <CsvTable text={text} name={entry.name} />;
}

/** A picture, with its own dimensions once the browser knows them — and an
    honest failure when the bytes are not the image the name promised. */
function ImageView({ url, name }: { url?: string; name: string }) {
  const [note, setNote] = useState<string | null>(null);
  const [broken, setBroken] = useState(false);
  if (broken) {
    return (
      <div className="wbox" style={{ padding: 16 }}>
        <p className="help" style={{ margin: 0 }}>
          This file is named like an image but the browser could not decode it.
          Download to open it in something that can.
        </p>
      </div>
    );
  }
  return (
    <>
      <div className="imgview">
        <img
          src={url}
          alt={name}
          onLoad={(e) =>
            setNote(`${e.currentTarget.naturalWidth} × ${e.currentTarget.naturalHeight} px`)}
          onError={() => setBroken(true)}
        />
      </div>
      {note && <p className="help" style={{ margin: "6px 0 0" }}>{note}</p>}
    </>
  );
}

/** The file, full-bleed, with the list still behind it — and the arrow keys
    still walking that list. Reading twenty files used to be twenty round trips
    through a table you had to find your place in again each time. */
function Preview({
  loaded, position, total, busy, onStep, onClose, onDownload,
}: {
  loaded: Loaded;
  position: number;
  total: number;
  busy: boolean;
  onStep: (delta: number) => void;
  onClose: () => void;
  onDownload: () => void;
}) {
  const [asSource, setAsSource] = useState(false);
  const [wrap, setWrap] = useState(false);
  const { entry } = loaded;
  const toggle = SOURCE_LABEL[loaded.kind];
  const wrappable = loaded.text !== undefined && (asSource || loaded.kind === "text");

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      const el = document.activeElement;
      const typing = el instanceof HTMLInputElement || el instanceof HTMLTextAreaElement;
      if (e.key === "Escape") { onClose(); return; }
      if (typing || e.metaKey || e.ctrlKey || e.altKey) return;
      if (e.key === "ArrowRight" || e.key === "j") { e.preventDefault(); onStep(1); }
      if (e.key === "ArrowLeft" || e.key === "k") { e.preventDefault(); onStep(-1); }
    }
    window.addEventListener("keydown", onKey);
    // The page behind the reader must not scroll under it.
    const previous = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      window.removeEventListener("keydown", onKey);
      document.body.style.overflow = previous;
    };
  }, [onClose, onStep]);

  return (
    <div className="modal-back" onClick={onClose}>
      <div
        className="modal reader"
        role="dialog"
        aria-modal="true"
        aria-label={entry.name}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="reader-head">
          <span className={`ftype ${FAMILY[entry.kind]}`}>{entry.ext.toUpperCase() || "FILE"}</span>
          <div className="reader-id">
            <div className="rt">
              <span className="fname">{entry.name}</span>
              {entry.state === "published" ? (
                <span className="chip accent">published</span>
              ) : (
                <span className="chip">working</span>
              )}
              {entry.version > 1 && <span className="chip">v{entry.version}</span>}
            </div>
            <div className="help">
              {entry.dir && <span className="mono">{entry.dir} · </span>}
              {fmtSize(entry.size)} · {entry.at ? STAMP.format(new Date(entry.at)) : "no date"}
              {entry.runId && ` · run ${entry.runId.slice(-8)}`}
              {entry.receipt?.satisfies.length
                ? ` · satisfies ${entry.receipt.satisfies.join(", ")}`
                : ""}
              {entry.receipt && (
                <>
                  {" · "}
                  <span className="mono" title={`sha256 ${entry.receipt.sha256}`}>
                    sha {entry.receipt.sha256.slice(0, 10)}…
                  </span>
                </>
              )}
            </div>
          </div>
          <div className="reader-nav">
            <button className="small" onClick={() => onStep(-1)} disabled={total < 2}
                    aria-label="Previous file">‹</button>
            <span>{fmtCount(position + 1)} of {fmtCount(total)}</span>
            <button className="small" onClick={() => onStep(1)} disabled={total < 2}
                    aria-label="Next file">›</button>
          </div>
        </div>
        <div className="reader-tools">
          {toggle && loaded.text !== undefined && (
            <button className="small" onClick={() => setAsSource(!asSource)}>
              {toggle[asSource ? 1 : 0]}
            </button>
          )}
          {wrappable && (
            <button className={`small${wrap ? " on" : ""}`} onClick={() => setWrap(!wrap)}>
              {wrap ? "unwrap lines" : "wrap lines"}
            </button>
          )}
          {busy && <span className="chip">opening…</span>}
          {loaded.shownBytes !== undefined && (
            <span className="chip warn">
              first {fmtSize(loaded.shownBytes)} of {fmtSize(entry.size)}
            </span>
          )}
          <div className="spacer" />
          <span className="help">← → to walk the list · Esc to close</span>
          <button className="small" onClick={onDownload}>↓ Download</button>
          <button className="small" onClick={onClose}>close</button>
        </div>
        <div className="reader-body">
          <PreviewBody loaded={loaded} asSource={asSource} wrap={wrap} />
        </div>
      </div>
    </div>
  );
}

// ── the browser ─────────────────────────────────────────────────────────────

type SortKey = "name" | "state" | "size" | "at" | "type";

/** Which direction a column should open in. A name or a type is read
    alphabetically; a size or a time is clicked because you want the biggest or
    the newest, and starting those at the smallest and oldest costs a second
    click every time. */
const FIRST_DESC: Record<SortKey, boolean> = {
  name: false, state: false, type: false, size: true, at: true,
};

const WINDOWS = [
  { key: "today", label: "Today", days: 0 },
  { key: "7d", label: "7 days", days: 6 },
  { key: "30d", label: "30 days", days: 29 },
  { key: "all", label: "All time", days: -1 },
] as const;

const STATES = [
  { key: "all", label: "All" },
  { key: "published", label: "Published" },
  { key: "working", label: "Working" },
] as const;

// Even one open folder can hold a thousand rows. The server sends a screenful
// of them (150) and the reader asks for the rest this many at a time; the
// count is on the folder either way.
const MORE_ROWS = 500;

/** The search box is the URL, so every keystroke lands there at once; what is
    *asked* of the server waits for a pause, or a six-letter word is six
    questions racing each other home. */
function useSettled<T>(value: T, ms: number): T {
  const [settled, setSettled] = useState(value);
  useEffect(() => {
    const timer = setTimeout(() => setSettled(value), ms);
    return () => clearTimeout(timer);
  }, [value, ms]);
  return settled;
}

/** Show the reader why this row matched. With a thousand files and a
    two-letter search, "which part of this is the hit" is a real question —
    and often the answer is the folder, not the name. */
function marked(text: string, terms: string[]) {
  if (terms.length === 0) return text;
  const pattern = new RegExp(`(${terms.map((t) => t.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|")})`, "gi");
  return text.split(pattern).map((part, i) =>
    terms.includes(part.toLowerCase()) ? <mark key={i}>{part}</mark> : part);
}

interface RowProps {
  entry: Entry;
  sub?: boolean;
  terms: string[];
  activeId: string | null;
  openingId: string | null;
  expanded: Record<string, boolean>;
  onOpen: (entry: Entry) => void;
  onSave: (entry: Entry) => void;
  onToggle: (id: string) => void;
}

/** One file. Defined here rather than inside the page on purpose: a component
    declared in a render is a new component *type* every time, so React throws
    away every row and builds it again — a thousand of them, four times a
    minute, on a page whose whole job is to be scrolled. */
function Row(props: RowProps) {
  const { entry, sub, terms, activeId, openingId, expanded, onOpen, onSave, onToggle } = props;
  const showing = expanded[entry.id] ?? false;
  return (
    <>
      <div
        className={`frow${sub ? " sub" : ""}${activeId === entry.id ? " on" : ""}`}
        onClick={() => onOpen(entry)}
      >
        <span className={`ftype ${FAMILY[entry.kind]}`}>{entry.ext.toUpperCase() || "—"}</span>
        <span className="fname-cell">
          {/* An earlier version does not need its name said again — it needs
              the two things the row above it cannot tell you: when it was
              published, and which run published it. */}
          <button className="fname" onClick={(e) => { e.stopPropagation(); onOpen(entry); }}>
            {sub ? (
              <span className="fdir">
                v{entry.version} · {entry.at ? fmtWhen(entry.at) : "no date"}
                {entry.runId && ` · run ${entry.runId.slice(-8)}`}
              </span>
            ) : (
              <>
                {entry.dir && <span className="fdir">{marked(`${entry.dir}/`, terms)}</span>}
                {marked(entry.name, terms)}
              </>
            )}
          </button>
          {entry.receipt?.satisfies.length ? (
            <span className="chip ok sat" title={`satisfies ${entry.receipt.satisfies.join(", ")}`}>
              satisfies {entry.receipt.satisfies.length === 1
                ? entry.receipt.satisfies[0]
                : `${entry.receipt.satisfies.length} requirements`}
            </span>
          ) : null}
          {entry.older.length > 0 && (
            <button
              className="chip vchip"
              aria-expanded={showing}
              onClick={(e) => { e.stopPropagation(); onToggle(entry.id); }}
            >
              v{entry.version}
              <span className="vmore"> · {showing ? "hide" : "history"}</span>
            </button>
          )}
          {openingId === entry.id && <span className="help"> opening…</span>}
        </span>
        <span className="fstate">
          {entry.state === "published" ? (
            <span className="chip accent">published</span>
          ) : (
            <span className="chip">working</span>
          )}
        </span>
        <span className="fsize num">{fmtSize(entry.size)}</span>
        <span className="fwhen" title={entry.at ? STAMP.format(new Date(entry.at)) : ""}>
          {entry.at ? TIME.format(new Date(entry.at)) : "—"}
        </span>
        <button
          className="fdl"
          title={`Download ${entry.name}`}
          aria-label={`Download ${entry.name}`}
          onClick={(e) => { e.stopPropagation(); onSave(entry); }}
        >
          ↓
        </button>
      </div>
      {showing && entry.older.map((old) => <Row key={old.id} {...props} entry={old} sub />)}
    </>
  );
}

/** Every file the project has, in one list, filed by the day it landed.
    Published deliverables and the agents' working files differ by a column
    rather than by a section, because "what did we make today" is one question
    and answering it used to mean reading two lists with different shapes.

    The list lives on the server. This page used to be handed every file and
    sort them itself, which stopped working at about the size of project
    whose files are worth a browser: the call slowed, the polls overlapped,
    and the tab stopped loading. Now it asks a question — these filters, these
    folders open, this far down — and gets back the folder headers and the
    rows it is showing. */
export default function Deliverables({ projectId }: { projectId: string }) {
  const [params, setParams] = useSearchParams();
  const [loaded, setLoaded] = useState<Loaded | null>(null);
  const [opening, setOpening] = useState<string | null>(null);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [sort, setSort] = useState<{ by: SortKey; desc: boolean }>({ by: "at", desc: true });
  const [toggled, setToggled] = useState<Record<string, boolean>>({});
  const [versionsOf, setVersionsOf] = useState<Record<string, boolean>>({});
  const [more, setMore] = useState<Record<string, number>>({});
  const searchRef = useRef<HTMLInputElement>(null);

  const query = params.get("q") ?? "";
  const state = (STATES.find((s) => s.key === params.get("state"))?.key ?? "all");
  const type = params.get("type") ?? "";
  const since = (WINDOWS.find((w) => w.key === params.get("since"))?.key ?? "all");
  const asked = useSettled(query, 250);

  function setParam(key: string, value: string, fallback: string) {
    const next = new URLSearchParams(params);
    if (value === fallback) next.delete(key);
    else next.set(key, value);
    // A filter is a view of the moment, not a place — replacing keeps Back
    // pointing at the page you came from rather than at your own keystrokes.
    setParams(next, { replace: true });
  }

  // "/" is the search box everywhere else on the web; it should be here too —
  // except while the reader is open, where the keyboard belongs to the file.
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (loaded) return;
      if (e.key !== "/" || e.metaKey || e.ctrlKey || e.altKey) return;
      const el = document.activeElement;
      if (el instanceof HTMLInputElement || el instanceof HTMLTextAreaElement) return;
      e.preventDefault();
      searchRef.current?.focus();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [loaded]);

  // An object URL is a live reference into the tab's memory; a reader who
  // walks fifty images would otherwise hold all fifty until the tab closed.
  useEffect(() => {
    const url = loaded?.objectUrl;
    return () => { if (url) URL.revokeObjectURL(url); };
  }, [loaded]);

  const base = `/observe/projects/${projectId}/files/browse`;
  // The question without the folders: the reader and the export ask it of
  // the whole list, the poll asks it of a screenful.
  const filters = useMemo(() => {
    const p = new URLSearchParams({ tz: TZ, sort: sort.by, desc: String(sort.desc) });
    if (asked.trim()) p.set("q", asked);
    if (state !== "all") p.set("state", state);
    if (type) p.set("type", type);
    if (since !== "all") p.set("since", since);
    return p.toString();
  }, [asked, state, type, since, sort]);
  const screen = useMemo(() => {
    const p = new URLSearchParams(filters);
    for (const [key, open] of Object.entries(toggled)) p.append(open ? "expand" : "collapse", key);
    for (const [key, rows] of Object.entries(more)) p.append("more", `${key}:${rows}`);
    return p.toString();
  }, [filters, toggled, more]);

  const { data, error } = usePoll(
    () => get<BrowseView>(`${base}?${screen}`).then((view) => ({ view, filters })),
    15000,
    [screen],
  );
  const view = data?.view ?? null;
  // What is on screen answers the last question asked, which is not always
  // the one in the box. Say so rather than show old rows as new ones.
  const settling = data !== null && (data.filters !== filters || asked !== query);

  const terms = useMemo(() => asked.toLowerCase().split(/\s+/).filter(Boolean), [asked]);
  const folders = useMemo(
    () => (view?.days ?? []).map((d) => ({
      ...d,
      label: dayLabel(d.key),
      entries: d.rows?.map((r) => toEntry(r, projectId)) ?? null,
    })),
    [view, projectId],
  );

  const filtering = query.trim() !== "" || state !== "all" || type !== "" || since !== "all";
  const total = view?.total ?? 0;
  const matched = view?.matched ?? 0;

  // Every open is numbered when it is asked for, and only the newest one may
  // put a file on screen. Each step is a round trip now, so a held arrow key
  // has several in the air, and they do not come home in the order they left;
  // `aim` is where the walk has got to, so the presses add up instead of all
  // stepping from the file still showing.
  const ticket = useRef(0);
  const aim = useRef<number | null>(null);
  const openEntry = useCallback(async (entry: Entry, mine = ++ticket.current) => {
    if (mine !== ticket.current) return;
    aim.current = entry.pos;
    setPreviewError(null);
    setOpening(entry.id);
    try {
      const blob = await fetchBytes(entry.url);
      const buffer = await blob.arrayBuffer();
      if (mine !== ticket.current) return;
      if (entry.kind === "image" || entry.kind === "pdf") {
        const mime = entry.kind === "pdf" ? "application/pdf" : IMAGE_MIME[entry.ext];
        setLoaded({
          entry, kind: entry.kind, blob,
          objectUrl: URL.createObjectURL(new Blob([buffer], { type: mime })),
        });
        return;
      }
      // A NUL byte in the head means binary, whatever the extension claimed.
      const head = new Uint8Array(buffer.slice(0, 8192));
      if (head.includes(0)) {
        setLoaded({ entry, kind: "binary", blob });
        return;
      }
      const shown = Math.min(buffer.byteLength, TEXT_LIMIT);
      setLoaded({
        entry,
        kind: entry.kind === "binary" ? "text" : entry.kind,
        blob,
        text: new TextDecoder().decode(new Uint8Array(buffer, 0, shown)),
        shownBytes: shown < buffer.byteLength ? shown : undefined,
      });
    } catch (e) {
      if (mine !== ticket.current) return;
      setPreviewError(`${entry.name}: ${(e as Error).message}`);
      setLoaded(null);
    } finally {
      if (mine === ticket.current) setOpening(null);
    }
  }, []);

  async function save(entry: Entry) {
    setPreviewError(null);
    try {
      download(await fetchBytes(entry.url), entry.name);
    } catch (e) {
      setPreviewError(`${entry.name}: ${(e as Error).message}`);
    }
  }

  // The reader walks what the filters found, not what happens to be unfolded
  // — pressing → into a closed folder is what you meant, every time. The next
  // file is asked for by position, so walking a list of fifty thousand never
  // needs the list.
  const step = useCallback(
    (delta: number) => {
      if (!loaded || matched === 0) return;
      const here = aim.current;
      // A file opened from a version list is not in the list; step from the top.
      const next = here === null ? 0 : (here + delta + matched) % matched;
      const mine = ++ticket.current;
      aim.current = next;
      setOpening("next");
      get<RowsView>(`${base}/rows?${filters}&offset=${next}&limit=1`)
        .then((page) => {
          if (mine !== ticket.current) return;
          if (page.rows[0]) return openEntry(toEntry(page.rows[0], projectId), mine);
          setOpening(null);
        })
        .catch((e: Error) => {
          if (mine !== ticket.current) return;
          setPreviewError(e.message);
          setOpening(null);
        });
    },
    [loaded, matched, base, filters, projectId, openEntry],
  );

  // "?open=<key>" arrives from the Overview's deliverable card, which knows
  // exactly which file the chairman pressed and used to throw it away — the
  // link landed him on this list to find it again himself. The server is
  // asked for that one file, so the link works however long the list is; the
  // parameter is consumed either way, so a reload does not reopen a file he
  // has since closed.
  const wanted = params.get("open");
  useEffect(() => {
    if (!wanted) return;
    const next = new URLSearchParams(params);
    next.delete("open");
    setParams(next, { replace: true });
    get<{ entry: FileRow }>(`${base}/entry?${filters}&id=${encodeURIComponent(wanted)}`)
      .then((found) => openEntry(toEntry(found.entry, projectId)))
      .catch((e: Error) =>
        setPreviewError(e instanceof ApiError && e.status === 404
          ? `That file is no longer published: ${wanted}`
          : e.message));
  }, [wanted]);

  // What this reader does with a list is take it away. The whole filtered
  // list, not the screenful — so it is asked for when the button is pressed.
  async function exportCsv() {
    setPreviewError(null);
    try {
      const page = await get<RowsView>(
        `${base}/rows?${filters}&limit=${Math.min(Math.max(matched, 1), 250_000)}&lean=true`,
      );
      downloadCsv(
        "deliverables.csv",
        ["File", "Folder", "State", "Type", "Size (bytes)", "Landed"],
        page.rows.map((r) => [
          r.name,
          r.dir,
          r.state === "published" ? "Published" : "Still being worked on",
          r.ext ? `.${r.ext}` : "",
          r.size,
          r.at ?? "",
        ]),
      );
    } catch (e) {
      setPreviewError(`Export: ${(e as Error).message}`);
    }
  }

  const dropped = view?.working_dropped ?? 0;

  const sortBtn = (by: SortKey, label: string, cls: string) => (
    <button
      className={`fsort ${cls}${sort.by === by ? " on" : ""}`.trim()}
      aria-sort={sort.by === by ? (sort.desc ? "descending" : "ascending") : "none"}
      onClick={() =>
        setSort((s) => (s.by === by ? { by, desc: !s.desc } : { by, desc: FIRST_DESC[by] }))
      }
    >
      {label}
      <span className={sort.by === by ? "arrow" : "arrow off"} aria-hidden="true">
        {sort.desc ? "▼" : "▲"}
      </span>
    </button>
  );

  return (
    <>
      <p className="subtitle">
        Every file the project has, filed by the day it landed. <b>Published</b> is what the
        client gets — explicitly published by a team, hashed and receipted. <b>Working</b> is the
        agents' shared scratch pad, kept in the same list so today is one screen rather than two.
      </p>
      {error && !view && <div className="error">{error}</div>}
      {previewError && <div className="error">{previewError}</div>}

      {!view ? (
        <Skeleton rows={6} />
      ) : total === 0 ? (
        <div className="wbox">
          <div className="wrow">
            <span className="badge">nothing yet</span>
            <div className="grow help">
              No files. A deliverable appears the moment a team publishes one — drafts included,
              so you can read the work as it takes shape instead of waiting for the end.
            </div>
          </div>
        </div>
      ) : (
        <>
          <div className="ftools">
            <div className="fsearch">
              <input
                ref={searchRef}
                type="search"
                value={query}
                onChange={(e) => setParam("q", e.target.value, "")}
                placeholder={`Search ${fmtCount(total)} files — name or folder  ( / )`}
                aria-label="Search files"
              />
            </div>
            <div className="viewtog" role="group" aria-label="Published or working">
              {STATES.map((s) => (
                <button
                  key={s.key}
                  className={state === s.key ? "on" : ""}
                  aria-pressed={state === s.key}
                  onClick={() => setParam("state", s.key, "all")}
                >
                  {s.label} <span className="n">{fmtCount(view.states[s.key])}</span>
                </button>
              ))}
            </div>
            <select
              className="ftypepick"
              value={type}
              aria-label="File type"
              onChange={(e) => setParam("type", e.target.value, "")}
            >
              <option value="">All types</option>
              {view.types.map(([ext, n]) => (
                <option key={ext} value={ext}>
                  {ext ? `.${ext}` : "no extension"} · {fmtCount(n)}
                </option>
              ))}
              {/* The chosen type can stop existing — the other filters moved,
                  or the last file of it was replaced. Keep it in the list
                  rather than letting the control go blank about its own
                  state. */}
              {type && !view.types.some(([ext]) => ext === type) && (
                <option value={type}>.{type} · 0</option>
              )}
            </select>
          </div>

          <div className="pills">
            {WINDOWS.map((w) => (
              <button
                key={w.key}
                className={since === w.key ? "active" : ""}
                aria-pressed={since === w.key}
                onClick={() => setParam("since", w.key, "all")}
              >
                {w.label}
                {w.key === "today" && view.today_total > 0 ? ` ${fmtCount(view.today_total)}` : ""}
              </button>
            ))}
            <span className="help">
              {fmtCount(matched)} of {fmtCount(total)} files ·{" "}
              {fmtCount(view.published_total)} published
            </span>
            {/* What this reader does with a list is take it away. Export was
                built one table at a time, which is why the record screens had
                it and the one holding the work did not. */}
            {matched > 0 && <ExportCsv onClick={exportCsv} />}
            {filtering && (
              <button
                className="linkish"
                onClick={() => setParams(new URLSearchParams(), { replace: true })}
              >
                clear filters
              </button>
            )}
          </div>

          {dropped > 0 && (
            <div className="notice-box">
              This project has more working files than one screen can hold. Showing the{" "}
              {fmtCount(view.working_kept)} most recently changed;{" "}
              {fmtCount(dropped)}{view.walk_stopped ? "+" : ""} older ones are not listed.
              Published deliverables are all here.
            </div>
          )}

          {matched === 0 ? (
            <div className="wbox">
              <div className="wrow">
                <span className="badge">no matches</span>
                <div className="grow help">
                  Nothing matches these filters.{" "}
                  <button
                    className="linkish"
                    onClick={() => setParams(new URLSearchParams(), { replace: true })}
                  >
                    Clear them
                  </button>{" "}
                  to see all {fmtCount(total)} files.
                </div>
              </div>
            </div>
          ) : (
            <div className={`wbox fbrowse${settling ? " settling" : ""}`} aria-busy={settling}>
              <div className="fhead">
                {sortBtn("type", "Type", "col-type")}
                {sortBtn("name", "Name", "col-name")}
                {sortBtn("state", "Status", "col-state")}
                {sortBtn("size", "Size", "num col-size")}
                {sortBtn("at", "Time", "col-when")}
                <span />
              </div>
              {folders.map((g) => {
                // A folder the reader just opened is open now, not when the
                // server next answers; its rows say so while they are on the way.
                const open = toggled[g.key] ?? g.open;
                const rows = g.open ? g.entries : null;
                return (
                  <div className="folder" key={g.key}>
                    <button
                      className="fdir-row"
                      aria-expanded={open}
                      onClick={() => setToggled({ ...toggled, [g.key]: !open })}
                    >
                      <span className={`caret${open ? " open" : ""}`} aria-hidden="true">▸</span>
                      <span className="fdir-name">{g.label}</span>
                      <span className="cnt">{fmtCount(g.count)}</span>
                      {g.published > 0 && (
                        <span className="chip accent">{fmtCount(g.published)} published</span>
                      )}
                    </button>
                    {open && rows === null && (
                      <div className="fmore"><span className="help">opening…</span></div>
                    )}
                    {open && rows !== null && (
                      <>
                        {rows.map((e) => (
                          <Row
                            key={e.id}
                            entry={e}
                            terms={terms}
                            activeId={loaded?.entry.id ?? null}
                            openingId={opening}
                            expanded={versionsOf}
                            onOpen={openEntry}
                            onSave={save}
                            onToggle={(id) =>
                              setVersionsOf((v) => ({ ...v, [id]: !v[id] }))}
                          />
                        ))}
                        {g.count > rows.length && (
                          <div className="fmore">
                            {(more[g.key] ?? 0) > g.cap ? (
                              <span className="help">loading…</span>
                            ) : (
                              <button
                                className="linkish"
                                onClick={() => setMore({ ...more, [g.key]: g.cap + MORE_ROWS })}
                              >
                                show {fmtCount(g.count - rows.length)} more from {g.label}
                              </button>
                            )}
                          </div>
                        )}
                      </>
                    )}
                  </div>
                );
              })}
            </div>
          )}
        </>
      )}

      {/* The age of the index, not of the last poll: the server may answer a
          poll from a walk a few seconds old, and this is where that is said. */}
      <AsOf at={view ? new Date(view.as_of) : null} error={error} />

      {loaded && (
        <Preview
          key={loaded.entry.id}
          loaded={loaded}
          position={loaded.entry.pos ?? 0}
          total={matched || 1}
          busy={opening !== null}
          onStep={step}
          onClose={() => {
            // A step still in the air must not reopen what was just closed.
            ticket.current += 1;
            setOpening(null);
            setLoaded(null);
          }}
          onDownload={() => download(loaded.blob, loaded.entry.name)}
        />
      )}
    </>
  );
}
