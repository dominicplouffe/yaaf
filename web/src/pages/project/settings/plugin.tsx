// Settings → Plugin version, on a project of a plugin kind: the version it runs
// on, and a move onto a newer one. A fixed plugin reaches a running project
// only this way; what was already accepted keeps its verdict.

import { useCallback, useEffect, useRef, useState } from "react";
import { Dashboard, get, PluginVersionRow, post, ProjectPlugin } from "../../../api";
import { ConfirmModal } from "../../../ui";
import { SectionHead } from "../../../settings/kit";
import { versionName } from "../../../ui/project";

export function PluginSection({ projectId, dash, onSaved, onError }: {
  projectId: string; dash: Dashboard; onSaved: (text: string) => void; onError: (text: string) => void;
}) {
  const [info, setInfo] = useState<ProjectPlugin | null>(null);
  const [target, setTarget] = useState<PluginVersionRow | null>(null);
  const [busy, setBusy] = useState(false);

  // The page hands a new onError every render; reading it through a ref keeps
  // the load to once per project rather than once per render.
  const onErrorRef = useRef(onError);
  onErrorRef.current = onError;
  const load = useCallback(() => {
    get<ProjectPlugin>(`/admin/projects/${projectId}/plugin`).then(setInfo)
      .catch((e) => onErrorRef.current((e as Error).message));
  }, [projectId]);
  useEffect(load, [load]);

  async function move() {
    if (!target) return;
    setBusy(true);
    try {
      await post(`/admin/projects/${projectId}/plugin`, { version: target.version });
      onSaved(`Moved to ${versionName(target)}. New work meets its rules; what was accepted stays.`);
      setTarget(null);
      load();
    } catch (e) {
      onError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <SectionHead title="Plugin version"
        lede="The version of the plugin this project runs on. Move it to a newer one to pick up a fix." />
      {info === null ? <p className="help">Loading…</p> : (
        <div className="set-card">
          <div className="set-row">
            <span className="set-row-lab">Runs on<span className="set-row-hint">{info.current.name}</span></span>
            <div className="set-row-val"><b>{versionName(info.current)}</b>{" "}
              <code title={info.current.version}>{info.current.version.slice(0, 12)}</code></div>
            <div className="set-row-act" />
          </div>
          {info.available.length === 0 ? (
            <div className="set-row">
              <span className="set-row-lab">Other versions</span>
              <div className="set-row-val help">None installed that this project can move to.</div>
              <div className="set-row-act" />
            </div>
          ) : info.available.map((v) => (
            <div className="set-row" key={v.id}>
              <span className="set-row-lab">{versionName(v)}
                <span className="set-row-hint">installed {new Date(v.installed_at).toLocaleDateString()}</span></span>
              <div className="set-row-val">
                {v.for_new_projects && <span className="badge good">New projects get this one</span>}{" "}
                <code title={v.version}>{v.version.slice(0, 12)}</code></div>
              <div className="set-row-act">
                {dash.can_chair
                  ? <button type="button" className="small primary" onClick={() => setTarget(v)}>Move to this</button>
                  : <span className="help">The chairman moves it</span>}
              </div>
            </div>
          ))}
        </div>
      )}
      {target && info && (
        <ConfirmModal title={`Move to ${versionName(target)}?`} confirmLabel="Move" busy={busy}
          onConfirm={move} onCancel={() => setTarget(null)}>
          <p>From {versionName(info.current)} to {versionName(target)}. Everything published from now on
            meets the new version's rules. What was already accepted keeps its verdict.</p>
        </ConfirmModal>
      )}
    </>
  );
}
