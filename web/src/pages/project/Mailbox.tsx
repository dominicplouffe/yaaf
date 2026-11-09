import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { del, get, patch, post, put } from "../../api";
import { fmtWhen, usePoll } from "../../hooks";
import { ConfirmModal, Drawer, fmtAgo, Skeleton } from "../../ui";
import { Avatar, EmptyState, Seg } from "../../ui/kit";
import { Icon } from "../../ui/Icon";
import { Why } from "../../settingsCopy";
import { ReopenThread } from "../../desk";
import { Flash, Group, SaveBar, SecretRow, SectionHead, Step, TestSeal, Toggle } from "../../settings/kit";

/** The project mailbox: an address the CEO can send a deliverable from and
    read the replies at. The server holds every rule — only the CEO writes,
    only to the list, a reply proves who sent it — so these screens are the
    configuration and the record, never a second place a rule could bend. */

export interface MailboxConfig {
  enabled?: boolean;
  address?: string;
  display_name?: string;
  smtp_host?: string;
  smtp_port?: number;
  smtp_security?: "starttls" | "ssl";
  imap_host?: string;
  imap_port?: number;
  imap_folder?: string;
  username?: string;
  recipients?: string[];
  verify_sender?: boolean;
  accept_new?: boolean;
}

interface ServerCheck { ok: boolean; error?: string }

export interface MailboxView {
  configured: boolean;
  enabled: boolean;
  missing: string[];
  config: MailboxConfig;
  password_set: boolean;
  recipients: { address: string; threads: number; last_at: string | null }[];
  needs_mail: boolean;
  start_refusal: string | null;
  /** The missing piece as a clause — "it has no email address yet". */
  start_state: string | null;
  status_now: string;
  status: {
    read_at: string | null;
    read_ok_at: string | null;
    read_error: string | null;
    ignored: number;
    queued: number;
    failed: number;
    last_test: ({ at: string; ok: boolean; smtp: ServerCheck; imap: ServerCheck }) | null;
    limits: { sends_per_day: number; messages_per_thread: number };
  };
}

export interface MailThreadRow {
  id: string;
  counterpart: string;
  subject: string;
  task_id: string | null;
  frozen: boolean;
  frozen_at: string | null;
  opened_by?: "ceo" | "them";
  messages: number;
  unread: number;
  failed: number;
  held: number;
  last_at: string | null;
  last: { direction: "out" | "in"; status: string; from: string; preview: string } | null;
}

interface MailMessageRow {
  id: string;
  direction: "out" | "in";
  status: string;
  from: string;
  to: string;
  subject: string;
  body: string;
  at: string;
  attachment?: { name?: string; version?: number; key?: string; size?: number };
  attachments?: { name: string; type: string; size: number | null; index: number;
    saved?: boolean; reason?: string; path?: string }[];
  error?: string;
}

const DEFAULTS: MailboxConfig = {
  smtp_port: 587, smtp_security: "starttls", imap_port: 993, imap_folder: "INBOX",
  verify_sender: true, enabled: true,
};

/** The server's own rule, said before the save instead of after it: one plain
    address, nothing a header parser could read as two. */
const ADDRESS = /^[a-z0-9!#$%&'*+/=?^_`{|}~.-]+@[a-z0-9-]+(\.[a-z0-9-]+)+$/;

/** A subject with its reply and forward prefixes taken off, so "Re: Q3 report"
    is not drawn as news under a thread called "Q3 report". */
const bare = (subject: string) => subject.replace(/^\s*((re|fwd?|aw|sv)\s*:\s*)+/i, "").trim().toLowerCase();

function fmtSize(bytes?: number): string {
  if (bytes === undefined) return "";
  return bytes < 1024 ? `${bytes} B` : bytes < 1048576 ? `${Math.round(bytes / 1024)} KB`
    : `${(bytes / 1048576).toFixed(1)} MB`;
}

// ── Settings → Email ───────────────────────────────────────────────────────────

export function MailboxSettings({ projectId }: { projectId: string }) {
  const [view, setView] = useState<MailboxView | null>(null);
  const [form, setForm] = useState<MailboxConfig>(DEFAULTS);
  const [password, setPassword] = useState("");
  const [passwordOpen, setPasswordOpen] = useState(false);
  const [list, setList] = useState<string[]>([]);
  const [adding, setAdding] = useState("");
  const [addError, setAddError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<{ ok: boolean; text: string } | null>(null);
  const [removing, setRemoving] = useState(false);

  /** The form follows the server on load and after every save — never while
      the person has unsaved changes, which a late poll would otherwise wipe. */
  const adopt = useCallback((next: MailboxView) => {
    setView(next);
    setForm({ ...DEFAULTS, accept_new: next.needs_mail, ...next.config });
    setList(next.config.recipients ?? []);
  }, []);

  const load = useCallback(() => {
    get<MailboxView>(`/admin/projects/${projectId}/mail`).then(adopt).catch(() => {});
  }, [projectId, adopt]);
  useEffect(load, [load]);

  const saved = view?.config ?? {};
  const configured = !!view?.configured;
  const savedList = saved.recipients ?? [];
  const FIELDS: (keyof MailboxConfig)[] = ["address", "display_name", "smtp_host", "smtp_port",
    "smtp_security", "imap_host", "imap_port", "imap_folder", "username", "verify_sender",
    "accept_new"];
  const dirty = !!password.trim() || list.join("\n") !== savedList.join("\n")
    || FIELDS.some((k) => k !== "accept_new"
      && (form[k] ?? DEFAULTS[k] ?? "") !== (saved[k] ?? DEFAULTS[k] ?? ""))
    || configured && !!form.accept_new !== !!saved.accept_new;

  const set = <K extends keyof MailboxConfig>(key: K, value: MailboxConfig[K]) =>
    setForm((f) => ({ ...f, [key]: value }));

  async function save(overrides: Partial<MailboxConfig> = {}, done?: string) {
    setBusy(true); setNote(null);
    try {
      const next = await put<MailboxView>(`/admin/projects/${projectId}/mail`, {
        ...form, ...overrides, recipients: list,
        ...(password.trim() ? { password: password.trim() } : {}),
      });
      setPassword(""); setPasswordOpen(false);
      adopt(next);
      setNote({ ok: true, text: done ?? (configured
        ? "Saved. The CEO's next run reads the new list."
        : "Email is set up. Test the connection to be sure the servers answer.") });
    } catch (e) { setNote({ ok: false, text: (e as Error).message }); } finally { setBusy(false); }
  }

  /** The on/off switch saves at once, and only itself: it does not commit
      half-typed edits in the form below it. */
  async function setOn(on: boolean) {
    setBusy(true); setNote(null);
    try {
      const next = await put<MailboxView>(`/admin/projects/${projectId}/mail`,
        { ...DEFAULTS, ...saved, enabled: on });
      setView(next);
      setNote({ ok: true, text: on ? "Email is on. The inbox is read every minute."
        : "Email is off. Nothing is sent or read; anything queued is reported as undelivered." });
    } catch (e) { setNote({ ok: false, text: (e as Error).message }); } finally { setBusy(false); }
  }

  /** Whether the project's work runs through email — the project's design, so
      it saves at once, like any switch in Settings. */
  async function setNeeded(on: boolean) {
    setBusy(true); setNote(null);
    try {
      await patch(`/admin/projects/${projectId}`, { needs_mail: on });
      load();
      setNote({ ok: true, text: on
        ? "Noted: this project's work runs through email. It starts, and resumes, only while its mailbox is on."
        : "Noted: email is optional for this project. It can start without a mailbox." });
    } catch (e) { setNote({ ok: false, text: (e as Error).message }); } finally { setBusy(false); }
  }

  async function test() {
    setBusy(true); setNote(null);
    try {
      const r = await post<{ ok: boolean; smtp: ServerCheck; imap: ServerCheck }>(
        `/admin/projects/${projectId}/mail/test`);
      setNote({ ok: r.ok, text: r.ok ? "Signed in to both servers. Nothing was sent."
        : [!r.smtp.ok && `Sending (SMTP): ${r.smtp.error}`, !r.imap.ok && `Reading (IMAP): ${r.imap.error}`]
          .filter(Boolean).join(" · ") });
      load();
    } catch (e) { setNote({ ok: false, text: (e as Error).message }); } finally { setBusy(false); }
  }

  function addRecipient() {
    const address = adding.trim().toLowerCase();
    if (!address) return;
    if (!ADDRESS.test(address)) { setAddError("That is not one email address."); return; }
    if (address === (form.address ?? "").trim().toLowerCase()) {
      setAddError("That is the project's own address — the CEO would be answering itself.");
      return;
    }
    if (list.includes(address)) { setAddError("Already on the list."); return; }
    setList([...list, address]); setAdding(""); setAddError(null);
  }

  const usage = useMemo(() => new Map((view?.recipients ?? []).map((r) => [r.address, r])), [view]);
  const removed = savedList.filter((a) => !list.includes(a));
  const status = view?.status;
  const test_ = status?.last_test;
  const lastTest = test_ ? {
    ok: test_.ok, at: test_.at,
    detail: test_.ok ? "both servers signed in" : [test_.smtp.error, test_.imap.error].filter(Boolean).join(" · "),
  } : null;
  const smtpOk = !!test_?.smtp.ok;
  const readOk = !!status?.read_ok_at && !status.read_error;

  if (view === null) return <Skeleton rows={4} />;

  return <>
    <SectionHead title="Email"
      lede="An address the CEO can send a finished deliverable from — to a person or to another agent — and read the reply at."
      actions={configured && <><TestSeal test={lastTest} />
        <button type="button" disabled={busy} onClick={test}>Test connection</button></>}>
      {view.start_refusal && view.status_now !== "running"
        ? <span className="mail-state"><span className="dot warn" aria-hidden="true" /><span>
            <b>This is what stands between the project and its start.</b> Its work runs
            through email, and {view.start_state}. Set it up below, test it, and start it.</span></span>
        : view.start_refusal
          ? <span className="mail-state"><span className="dot critical" aria-hidden="true" /><span>
              <b>This project&apos;s work runs through email, and none is on</b> — the CEO can neither
              receive nor send it, and has been told.</span></span>
        : !configured ? <span>Use a mailbox made for this project. Mail goes through its own servers, never the platform&apos;s alert mail.</span>
        : !view.enabled ? <span className="mail-state"><span className="dot warn" aria-hidden="true" /><span>{view.missing.length
          ? <>Still needs <b>{view.missing.join(", ")}</b>.</> : <>Off. Nothing is sent or read.</>}</span></span>
          : status?.read_error ? <span className="mail-state"><span className="dot critical" aria-hidden="true" /><span>
            The inbox cannot be read, so replies are not reaching the CEO: {status.read_error}</span></span>
            : <span className="mail-state"><span className="dot good" aria-hidden="true" /><span>Sending as <b>{saved.address}</b>
              {status?.read_ok_at ? <> · inbox read {fmtAgo(status.read_ok_at)}</> : <> · the inbox is read every minute</>}
              {status && status.queued > 0 ? <> · <b>{status.queued}</b> queued</> : null}
              {status && status.failed > 0 ? <> · <Link to={`/projects/${projectId}/details/activity/mail`}><b>{status.failed}</b> undelivered</Link></> : null}
              {status && status.ignored > 0 ? <> · {status.ignored} unrelated message{status.ignored === 1 ? "" : "s"} left alone</> : null}
            </span></span>}
    </SectionHead>
    <Flash note={note} />

    {configured && (
      <div className="set-card ladder" style={{ marginBottom: "var(--s6)" }}>
        <Step done={view.enabled && smtpOk} n={1} title="Send">
          {view.enabled && smtpOk ? <>The CEO&apos;s mail leaves from <b>{saved.address}</b>.</>
            : view.enabled ? "Saved, not tested yet — press Test connection." : "Off."}</Step>
        <Step done={view.enabled && readOk} n={2} title="Hear back">
          {view.enabled && readOk ? "Replies are read every minute and wake the CEO."
            : status?.read_error ? "The inbox cannot be read." : "Waiting for the first read."}</Step>
        <Step done={savedList.length > 0} n={3} title="Know who to write to">
          {savedList.length ? <>{savedList.length} address{savedList.length === 1 ? "" : "es"} on the list.</>
            : "Nobody yet — the CEO can write to no one."}</Step>
      </div>
    )}

    <div className="set-card pad set-switches" style={{ marginBottom: "var(--s6)" }}>
      <Toggle id="mail-needed" checked={view.needs_mail} disabled={busy} onChange={setNeeded}
        label="This project's work runs through email"
        sub={view.needs_mail
          ? "It waits for mail, answers it, or sends its work out by it — so it starts, and resumes, only while its mailbox is on."
          : "Off, email is something the CEO may use, and the project starts without it."} />
      {configured && !view.missing.length && (
        <Toggle id="mail-on" checked={view.enabled} disabled={busy} onChange={setOn}
          label="Email is on"
          sub={view.enabled ? "The CEO may send, and the inbox is read every minute."
            : view.needs_mail ? <span className="warn-ink">Off — and this project&apos;s work runs through email, so it cannot start or resume.</span>
            : "Nothing is sent or read. Turning it on changes nothing else."} />
      )}
    </div>

    <Group title="Address">
      <div className="set-card pad set-fields">
        <div className="field-wrap">
          <label className="field" htmlFor="mail-address">Project email address</label>
          <input id="mail-address" value={form.address ?? ""} placeholder="ceo@yourcompany.com"
            autoComplete="off" disabled={busy} onChange={(e) => set("address", e.target.value)} />
          <span className="help">The From line, and where replies arrive.</span>
        </div>
        <div className="field-wrap">
          <label className="field" htmlFor="mail-name">Sender name</label>
          <input id="mail-name" value={form.display_name ?? ""} placeholder="Optional, e.g. Acme"
            disabled={busy} onChange={(e) => set("display_name", e.target.value)} />
          <span className="help">What the recipient sees beside the address.</span>
        </div>
      </div>
    </Group>

    <Group title="Servers">
      <div className="set-card pad set-fields">
        <div className="field-wrap">
          <label className="field" htmlFor="mail-smtp">Sending — SMTP server</label>
          <input id="mail-smtp" value={form.smtp_host ?? ""} placeholder="smtp.yourcompany.com"
            disabled={busy} onChange={(e) => set("smtp_host", e.target.value)} />
          <div className="mail-port">
            <Seg value={form.smtp_security ?? "starttls"} onPick={(v) => setForm((f) => ({
              ...f, smtp_security: v, smtp_port: v === "ssl" ? 465 : 587 }))}
              options={[{ key: "starttls", label: "STARTTLS" }, { key: "ssl", label: "TLS" }]} />
            <label className="sr-only" htmlFor="mail-smtp-port">SMTP port</label>
            <input id="mail-smtp-port" type="number" inputMode="numeric" value={form.smtp_port ?? 587}
              disabled={busy} onChange={(e) => set("smtp_port", Number(e.target.value))} />
          </div>
          <span className="help">{form.smtp_security === "ssl"
            ? "TLS from the first byte — usually port 465." : "Upgrades to TLS before signing in — usually port 587."}</span>
        </div>
        <div className="field-wrap">
          <label className="field" htmlFor="mail-imap">Reading — IMAP server</label>
          <input id="mail-imap" value={form.imap_host ?? ""} placeholder="imap.yourcompany.com"
            disabled={busy} onChange={(e) => set("imap_host", e.target.value)} />
          <div className="mail-port">
            <label className="sr-only" htmlFor="mail-folder">Folder</label>
            <input id="mail-folder" value={form.imap_folder ?? "INBOX"} disabled={busy}
              onChange={(e) => set("imap_folder", e.target.value)} />
            <label className="sr-only" htmlFor="mail-imap-port">IMAP port</label>
            <input id="mail-imap-port" type="number" inputMode="numeric" value={form.imap_port ?? 993}
              disabled={busy} onChange={(e) => set("imap_port", Number(e.target.value))} />
          </div>
          <span className="help">Folder and port, always over TLS. Mail is read without being marked read.</span>
        </div>
      </div>
    </Group>

    <Group title="Sign-in">
      <div className="set-card">
        <div className="set-row">
          <label className="set-row-lab" htmlFor="mail-user">Username
            <span className="set-row-hint">Usually the address itself</span></label>
          <div className="set-row-val"><input id="mail-user" value={form.username ?? ""} autoComplete="off"
            disabled={busy} placeholder={form.address || "ceo@yourcompany.com"}
            onChange={(e) => set("username", e.target.value)} /></div>
          <div className="set-row-act" />
        </div>
        <SecretRow id="mail-pass" label="Password"
          hint="Used for both servers. Gmail needs an app password."
          saved={view.password_set} unlocks="needed to send or read" value={password}
          onChange={setPassword} open={passwordOpen || !view.password_set && !!password}
          setOpen={setPasswordOpen} placeholder="App password" disabled={busy} />
      </div>
      <Why label="Which password?">
        Gmail: turn on 2-step verification, then make an app password and paste it here. Microsoft
        365 may refuse passwords for SMTP and IMAP altogether; its administrator can turn SMTP AUTH
        and IMAP back on for this one mailbox. The password is stored encrypted and no agent is ever
        given it — the platform signs in and sends the CEO&apos;s mail itself.
      </Why>
    </Group>

    <Group title="Who the CEO may email" count={list.length || undefined}>
      <div className="set-card">
        {list.length === 0 && removed.length === 0 && (
          <div className="set-row"><span className="set-row-lab">Nobody yet</span>
            <div className="set-row-val">Add the people or agents the CEO may send work to. Every other address is refused.</div>
            <div className="set-row-act" /></div>
        )}
        {list.map((address) => {
          const used = usage.get(address);
          return (
            <div className="set-row mail-rcpt" key={address}>
              <span className="set-row-lab mail-addr">{address}</span>
              <div className="set-row-val">{used && used.threads > 0
                ? <>{used.threads} thread{used.threads === 1 ? "" : "s"} · last {fmtAgo(used.last_at)}</>
                : savedList.includes(address) ? "Not written to yet" : <span className="help">Added — save to allow it</span>}</div>
              <div className="set-row-act">
                <button type="button" className="small" disabled={busy}
                  aria-label={`Remove ${address}`}
                  onClick={() => setList(list.filter((a) => a !== address))}>Remove</button>
              </div>
            </div>
          );
        })}
        {removed.map((address) => {
          const used = usage.get(address);
          return (
            <div className="set-row mail-rcpt mail-gone" key={`gone-${address}`}>
              <span className="set-row-lab mail-addr"><s>{address}</s></span>
              <div className="set-row-val">{used && used.threads > 0
                ? <span className="warn-ink">Saving ends the CEO&apos;s conversation{used.threads === 1 ? "" : "s"} with them.</span>
                : "Removed — save to apply."}</div>
              <div className="set-row-act">
                <button type="button" className="small" disabled={busy}
                  onClick={() => setList([...list, address])}>Undo</button>
              </div>
            </div>
          );
        })}
        <form className="set-row editing" onSubmit={(e) => { e.preventDefault(); addRecipient(); }}>
          <label className="set-row-lab" htmlFor="mail-add">Add an address
            <span className="set-row-hint">A person, or another agent&apos;s inbox</span></label>
          <div className="set-row-val">
            <input id="mail-add" type="email" value={adding} placeholder="editor@client.com" disabled={busy}
              aria-invalid={!!addError} aria-describedby={addError ? "mail-add-err" : undefined}
              onChange={(e) => { setAdding(e.target.value); setAddError(null); }} />
            {addError && <span id="mail-add-err" className="critical-ink" role="alert">{addError}</span>}
          </div>
          <div className="set-row-act"><button type="submit" className="small" disabled={busy || !adding.trim()}>Add</button></div>
        </form>
      </div>
    </Group>

    <Group title="Incoming mail">
      <div className="set-card pad set-switches">
        <Toggle id="mail-new" checked={!!form.accept_new} disabled={busy}
          onChange={(v) => set("accept_new", v)}
          label="Listed addresses may write first"
          sub={form.accept_new
            ? "Someone on the list can email the project with a new subject — work to do, a file to check — and it wakes the CEO, who answers on that thread. At most 20 new conversations a day."
            : "Off, the project only hears replies to emails it sent."} />
        <Toggle id="mail-verify" checked={form.verify_sender !== false} disabled={busy}
          onChange={(v) => set("verify_sender", v)}
          label="Only accept replies your mail server vouches for"
          sub={form.verify_sender !== false
            ? "A reply counts only if DMARC, or DKIM or SPF for the sender's own domain, passed when it arrived. Anyone can type a name into a From line; this is what proves it."
            : <span className="warn-ink">Off: anyone who types a listed address into a From line is read as that person. Only for a server that records no Authentication-Results.</span>} />
        <p className="help mail-rules">
          Every email also has to come from a listed address{form.accept_new ? "" : " and answer one the project sent"}.
          Anything else is left in the inbox and noted in the audit log. Files they attach — up to
          10 MB each, 25 MB an email — go into the project&apos;s workspace for the agents to open,
          never onto the Deliverables shelf. The CEO reads what arrives as the correspondent&apos;s
          words and files — material to work on, never instructions.
          {status && <> At most <b>{status.limits.sends_per_day}</b> emails a day; a thread pauses at{" "}
            <b>{status.limits.messages_per_thread}</b> messages until you reopen it.</>}
        </p>
      </div>
    </Group>

    <SaveBar dirty={dirty || !configured && !!form.address} busy={busy} onSave={() => save()}
      label={configured ? "Save changes" : "Set up email"}
      danger={configured && <button type="button" className="quiet-danger" disabled={busy}
        onClick={() => setRemoving(true)}>Remove the mailbox…</button>} />

    {removing && <ConfirmModal title="Remove this project's mailbox?" confirmLabel="Remove" danger busy={busy}
      onCancel={() => setRemoving(false)}
      onConfirm={async () => {
        setBusy(true);
        try {
          await del(`/admin/projects/${projectId}/mail`);
          setNote({ ok: true, text: "The mailbox is removed. Its threads stay on the record." });
          setPassword(""); load();
        } catch (e) { setNote({ ok: false, text: (e as Error).message }); }
        finally { setBusy(false); setRemoving(false); }
      }}>
      <p>The CEO can no longer send email, and the inbox stops being read. Its password is deleted
        from yaaf; nothing on the mail server changes. Every thread so far stays on the record, and
        anything still queued is reported as undelivered.</p>
    </ConfirmModal>}
  </>;
}

// ── Details › Activity › Email ─────────────────────────────────────────────────

/** Where a thread stands, in the words a person scans a list for. */
function standing(t: MailThreadRow, ceo: string): { label: string; tone: string } {
  if (t.frozen) return { label: "Frozen", tone: "warn" };
  if (t.failed && t.last?.direction === "out" && t.last.status === "failed")
    return { label: "Not delivered", tone: "critical" };
  if (t.unread) return { label: t.opened_by === "them" && t.messages === t.unread
    ? `New — waiting on ${ceo}` : `Reply waiting on ${ceo}`, tone: "accent" };
  if (t.last?.direction === "in") return { label: `${ceo} has read it`, tone: "" };
  if (t.last?.status === "queued") return { label: "Sending", tone: "" };
  return { label: "Waiting on them", tone: "" };
}

export function MailThreads({ projectId, ceoName }: { projectId: string; ceoName: string }) {
  const { data, error, reload } = usePoll(
    () => get<{ threads: MailThreadRow[]; mailbox: boolean; enabled: boolean }>(
      `/observe/projects/${projectId}/mail`), 20000, [projectId]);
  const [params, setParams] = useSearchParams();
  const open = params.get("open");
  const setOpen = (id: string | null) => {
    const next = new URLSearchParams(params);
    if (id) next.set("open", id); else next.delete("open");
    setParams(next, { replace: !id });
  };

  if (error) return <div className="card" style={{ marginTop: "var(--s3)" }}><p className="error">{error}</p></div>;
  if (!data) return <div style={{ marginTop: "var(--s3)" }}><Skeleton rows={4} /></div>;

  const settings = `/projects/${projectId}/settings?s=mail`;
  if (data.threads.length === 0) {
    return (
      <div className="card" style={{ marginTop: "var(--s3)" }}>
        {!data.mailbox ? (
          <EmptyState icon="mail" title="This project has no email address"
            foot={<Link to={settings}>Set up email <Icon name="arrow" size={13} /></Link>}>
            Give it one and the CEO can send a finished deliverable to someone — a person or another
            agent — and read the reply here.
          </EmptyState>
        ) : !data.enabled ? (
          <EmptyState icon="mail" title="Email is off"
            foot={<Link to={settings}>Open Settings → Email <Icon name="arrow" size={13} /></Link>}>
            Nothing is sent or read until it is on.
          </EmptyState>
        ) : (
          <EmptyState icon="mail" title="The CEO has not emailed anyone yet"
            foot={<Link to={settings}>Who it may write to <Icon name="arrow" size={13} /></Link>}>
            Each conversation appears here as soon as it sends the first email, with every reply.
          </EmptyState>
        )}
      </div>
    );
  }

  const frozen = data.threads.filter((t) => t.frozen).length;
  const waiting = data.threads.filter((t) => t.unread > 0).length;
  const failed = data.threads.filter((t) => standing(t, ceoName).label === "Not delivered").length;
  const opened = data.threads.find((t) => t.id === open) ?? null;

  return (
    <div style={{ marginTop: "var(--s3)" }}>
      <div className="listbar">
        <span className="help">{data.threads.length} conversation{data.threads.length === 1 ? "" : "s"}</span>
        {frozen > 0 && <span className="chip warn">{frozen} frozen — yours to reopen</span>}
        {failed > 0 && <span className="chip critical">{failed} not delivered</span>}
        {waiting > 0 && <span className="chip accent">{waiting} waiting on {ceoName}</span>}
        <span className="grow" />
        <Link className="btnlink small" to={settings}><Icon name="gear" size={13} /> Email settings</Link>
      </div>
      <div className="vlist" role="table" aria-label="Email conversations">
        <div className="vrow vhead mrow" role="row">
          <span role="columnheader">Conversation</span>
          <span role="columnheader">Where it stands</span>
          <span role="columnheader" className="num">Messages</span>
          <span role="columnheader" className="vupd">Last</span>
        </div>
        {data.threads.map((t) => {
          const s = standing(t, ceoName);
          const pick = () => setOpen(t.id);
          return (
            <div key={t.id} role="row" tabIndex={0} aria-label={`${t.subject}, with ${t.counterpart}`}
                 className={`vrow click mrow${open === t.id ? " sel" : ""}`} onClick={pick}
                 onKeyDown={(e) => {
                   if (e.target === e.currentTarget && (e.key === "Enter" || e.key === " ")) {
                     e.preventDefault(); pick();
                   }
                 }}>
              <span className="vname">
                <Avatar name={t.counterpart} team={t.counterpart} size="sm" />
                <span className="vname-text">
                  <span className="mrow-subj">{t.subject}</span>
                  <span className="vsub">{t.counterpart}{t.last?.preview
                    ? <> · {t.last.direction === "out" ? `${ceoName}: ` : ""}{t.last.preview}</> : null}</span>
                </span>
              </span>
              <span><span className={`chip${s.tone ? ` ${s.tone}` : ""}`}>{s.label}</span></span>
              <span className="num">{t.messages}</span>
              <span className="vupd">{fmtAgo(t.last_at)}</span>
            </div>
          );
        })}
      </div>
      {open && <MailThreadDrawer projectId={projectId} threadId={open} row={opened} ceoName={ceoName}
                                 onClose={() => setOpen(null)} onChanged={reload} />}
    </div>
  );
}

function MailThreadDrawer({ projectId, threadId, row, ceoName, onClose, onChanged }: {
  projectId: string; threadId: string; row: MailThreadRow | null; ceoName: string;
  onClose: () => void; onChanged: () => void;
}) {
  const [messages, setMessages] = useState<MailMessageRow[] | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const load = useCallback(() => {
    get<{ messages: MailMessageRow[] }>(`/observe/projects/${projectId}/mail/${threadId}`)
      .then((t) => { setMessages(t.messages); setProblem(null); })
      .catch((e) => setProblem((e as Error).message));
  }, [projectId, threadId]);
  useEffect(() => { setMessages(null); load(); }, [load]);

  const shown = messages ?? [];
  const held = messages?.filter((m) => m.status === "held").length ?? 0;

  return (
    <Drawer wide onClose={onClose}
      icon={<span className="vkind"><Icon name="mail" size={15} /></span>}
      title={row?.subject ?? "Email thread"}
      sub={row ? <>With <b>{row.counterpart}</b> · {row.messages} message{row.messages === 1 ? "" : "s"}
        {row.task_id ? <> · <Link to={`/projects/${projectId}/details/tasks`}>its task</Link></> : null}</> : undefined}
      footer={row?.frozen ? (
        <div className="mail-foot">
          <span className="help">Reopening hands {held ? `the ${held} held repl${held === 1 ? "y" : "ies"}` : "the thread"} back
            to {ceoName} and allows another round before it stops again.</span>
          <ReopenThread projectId={projectId} threadId={threadId} primary
            onDone={() => { onChanged(); load(); }} />
        </div>
      ) : (
        <span className="help">Only {ceoName} writes on this thread, and only to addresses on your list.</span>
      )}>
      {row?.frozen && (
        <div className="mail-frozen" role="status">
          <Icon name="alert" size={16} />
          <span><b>Frozen{row.frozen_at ? ` ${fmtAgo(row.frozen_at)}` : ""}.</b> It reached the limit of
            messages one thread may hold, so two correspondents cannot answer each other forever.
            {held ? ` ${held} repl${held === 1 ? "y is" : "ies are"} held, unread by ${ceoName}.` : ""}</span>
        </div>
      )}
      {problem && <p className="error" role="alert">{problem}</p>}
      {messages === null && !problem ? <Skeleton rows={3} /> : (
        <>
          <ol className="mail-thread">
            {shown.map((m) => {
              const ceo = m.direction === "out";
              const tone = m.status === "failed" ? "critical" : m.status === "held" || m.status === "queued" ? "warn" : "";
              const word = { sent: "Sent", queued: "Sending", failed: "Not delivered", received: "Received",
                held: "Held — thread frozen" }[m.status] ?? m.status;
              return (
                <li key={m.id} className={`mailmsg ${ceo ? "out" : "in"}`}>
                  <header className="mailmsg-head">
                    <Avatar name={ceo ? ceoName : m.from} team={ceo ? ceoName : m.from} size="sm" />
                    <span className="mailmsg-who"><b>{ceo ? ceoName : m.from}</b>
                      <span className="vsub">{ceo ? <>CEO · to {m.to}</> : <>to {ceoName}</>} · <time dateTime={m.at} title={fmtWhen(m.at)}>{fmtAgo(m.at)}</time></span>
                    </span>
                    {(tone || m.status !== "sent" && m.status !== "received") &&
                      <span className={`chip${tone ? ` ${tone}` : ""}`}>{word}</span>}
                  </header>
                  {bare(m.subject) !== bare(row?.subject ?? "") && <p className="mailmsg-subj">{m.subject}</p>}
                  <div className="mailmsg-body">{m.body}</div>
                  {m.attachment?.name && (
                    <div className="mailmsg-att">
                      {m.attachment.key
                        ? <Link className="gchip" to={`/projects/${projectId}/details/deliverables?open=${encodeURIComponent(m.attachment.key)}`}>
                            <Icon name="file" size={12} />{m.attachment.name}
                            {m.attachment.version && m.attachment.version > 1 ? ` · v${m.attachment.version}` : ""}
                            {m.attachment.size !== undefined ? ` · ${fmtSize(m.attachment.size)}` : ""}</Link>
                        : <span className="gchip"><Icon name="file" size={12} />{m.attachment.name}</span>}
                    </div>
                  )}
                  {m.attachments?.length ? (
                    <div className="mailmsg-att">
                      {m.attachments.map((a) => a.saved ? (
                        <a key={a.index} className="gchip" download={a.name}
                           href={`/observe/projects/${projectId}/mail/${threadId}/files/${m.id}/${a.index}`}
                           title={`In the project workspace at ${a.path} — any agent can open it`}>
                          <Icon name="download" size={12} />{a.name}
                          {a.size ? ` · ${fmtSize(a.size)}` : ""}</a>
                      ) : (
                        <span key={a.index} className="gchip dim" title={`Not kept: ${a.reason ?? "unknown"}`}>
                          <Icon name="file" size={12} />{a.name} · not kept ({a.reason})</span>
                      ))}
                      {m.attachments.some((a) => a.saved) && (
                        <span className="help">In the workspace under <code>mail-inbox/</code> for the agents to open.</span>
                      )}
                    </div>
                  ) : null}
                  {m.error && <p className="critical-ink mailmsg-err"><Icon name="alert" size={13} /> {m.error}</p>}
                </li>
              );
            })}
          </ol>
        </>
      )}
    </Drawer>
  );
}
