import { useState } from "react";
import { get, getToken } from "../../api";
import { fmtWhen, usePoll } from "../../hooks";

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

interface FilesView {
  workspace: WorkspaceFile[];
  artifacts: ArtifactFile[];
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

export default function Files({ projectId }: { projectId: string }) {
  const { data, error } = usePoll(
    () => get<FilesView>(`/observe/projects/${projectId}/files`),
    15000,
  );
  const [preview, setPreview] = useState<{ name: string; text: string } | null>(null);
  const [previewError, setPreviewError] = useState<string | null>(null);

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
      setPreview({ name, text });
    } catch (e) {
      setPreviewError((e as Error).message);
    }
  }

  if (error) return <div className="error">{error}</div>;

  const workspace = data?.workspace ?? [];
  const artifacts = data?.artifacts ?? [];

  return (
    <>
      <p className="subtitle">
        Everything the agents have written. The workspace is their shared working directory —
        drafts and work in progress, changing as they work. Artifacts are finished deliverables
        they explicitly published. Files open as text previews here, or download when binary.
      </p>
      {previewError && <div className="error">{previewError}</div>}

      <h2>Workspace — work in progress ({workspace.length} file{workspace.length === 1 ? "" : "s"})</h2>
      <div className="card">
        {workspace.length === 0 ? (
          <p className="help">Nothing yet — agents write here once they start working.</p>
        ) : (
          <table>
            <thead>
              <tr>
                <th style={{ width: "50%" }}>File</th>
                <th>Size</th>
                <th>Changed</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {workspace.map((file) => (
                <tr key={file.path}>
                  <td className="mono">{file.path}</td>
                  <td>{fmtSize(file.size)}</td>
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
        )}
      </div>

      <h2>Artifacts — published deliverables ({artifacts.length})</h2>
      <div className="card">
        {artifacts.length === 0 ? (
          <p className="help">
            None yet — agents publish finished work here with their artifact tool; the
            workspace above is where it takes shape first.
          </p>
        ) : (
          <table>
            <thead>
              <tr>
                <th style={{ width: "40%" }}>Deliverable</th>
                <th>From run</th>
                <th>Size</th>
                <th>Published</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {artifacts.map((artifact) => (
                <tr key={artifact.key}>
                  <td className="mono">{artifact.name}</td>
                  <td className="mono">{artifact.run_id ? artifact.run_id.slice(-8) : "—"}</td>
                  <td>{fmtSize(artifact.size)}</td>
                  <td className="mono">{artifact.modified ? fmtWhen(artifact.modified) : "—"}</td>
                  <td>
                    <button
                      className="small"
                      onClick={() =>
                        open(
                          `/observe/projects/${projectId}/files/content?artifact=${encodeURIComponent(artifact.key)}`,
                          artifact.name,
                          artifact.size,
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
        )}
      </div>

      {preview && (
        <>
          <h2>
            {preview.name}{" "}
            <button className="small" onClick={() => setPreview(null)}>
              close
            </button>
          </h2>
          <pre className="block" style={{ maxHeight: "60vh", overflow: "auto" }}>
            {preview.text}
          </pre>
        </>
      )}
    </>
  );
}
