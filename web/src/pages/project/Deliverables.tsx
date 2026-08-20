import { useEffect, useRef, useState } from "react";
import { get, getToken } from "../../api";
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

/** What the agents published, reframed around what it is rather than where it
    was stored. A deck a chairman cannot look at is not a deliverable, so HTML
    renders as itself; the source is a toggle, not the only option. */
export default function Deliverables({ projectId }: { projectId: string }) {
  const { data, error, at } = usePoll(
    () => get<FilesView>(`/observe/projects/${projectId}/files`),
    15000,
  );
  const [preview, setPreview] = useState<{ name: string; text: string; html: boolean } | null>(null);
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
      const html = ["html", "htm"].includes(extOf(name));
      setAsSource(false);
      setPreview({ name, text, html });
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
            {preview.html && (
              <button className="small" onClick={() => setAsSource(!asSource)}>
                {asSource ? "show the page" : "view source"}
              </button>
            )}
            <button className="small" onClick={() => setPreview(null)}>close</button>
          </h2>
          {preview.html && !asSource ? (
            // Agent-written HTML runs in an opaque origin: scripts work so the
            // charts render, but it can never reach this app's session.
            <iframe
              className="preview-frame"
              title={preview.name}
              sandbox="allow-scripts"
              srcDoc={preview.text}
            />
          ) : (
            <pre className="block" style={{ maxHeight: "60vh", overflow: "auto" }}>
              {preview.text}
            </pre>
          )}
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
