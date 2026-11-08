import { useCallback, useEffect, useRef, useState } from "react";
import { del, get, post, put } from "../../api";
import { fmtWhen, usePoll } from "../../hooks";
import { SecretInput, Skeleton } from "../../ui";
import { Lede, Why } from "../../settingsCopy";

/** The project mailbox: an address the CEO writes from, and the threads it has
    had. The server holds every rule — who may be written to, that only the CEO
    writes, that a reply proves who sent it — so this page is configuration and
    a record, never a second place where a rule could be bent. */

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
}

interface ServerCheck {
  ok: boolean;
  error?: string;
}

export interface MailboxView {
  configured: boolean;
  enabled: boolean;
  missing: string[];
  config: MailboxConfig;
  password_set: boolean;
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
  messages: number;
  unread: number;
  last_at: string | null;
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
  attachment?: { name?: string; version?: number };
  attachments?: { name: string; type: string; size: number | null }[];
  error?: string;
}

type Act = (
  fn: () => Promise<unknown>,
  ok: string | ((result: unknown) => string),
) => Promise<void>;

/** Settings → Email. */
export function MailboxSettings({ projectId, act, busy }: {
  projectId: string;
  act: Act;
  busy: boolean;
}) {
  const [view, setView] = useState<MailboxView | null>(null);
  const [form, setForm] = useState<MailboxConfig>({});
  const [password, setPassword] = useState("");
  const [recipients, setRecipients] = useState("");
  const [testing, setTesting] = useState(false);
  // What the person has typed outranks a load that lands after they started:
  // the form follows the server only until it is touched, and again after a save.
  const dirty = useRef(false);

  const adopt = useCallback((next: MailboxView) => {
    setView(next);
    if (dirty.current) return;
    setForm({ smtp_port: 587, smtp_security: "starttls", imap_port: 993,
              imap_folder: "INBOX", verify_sender: true, enabled: true, ...next.config });
    setRecipients((next.config.recipients ?? []).join("\n"));
  }, []);

  const load = useCallback(() => {
    get<MailboxView>(`/admin/projects/${projectId}/mail`).then(adopt).catch(() => {});
  }, [projectId, adopt]);

  useEffect(load, [load]);

  const set = <K extends keyof MailboxConfig>(key: K, value: MailboxConfig[K]) => {
    dirty.current = true;
    setForm((f) => ({ ...f, [key]: value }));
  };

  function body(overrides: Partial<MailboxConfig> = {}) {
    return {
      ...form,
      ...overrides,
      recipients: recipients.split(/[\s,;]+/).map((r) => r.trim()).filter(Boolean),
      ...(password.trim() ? { password: password.trim() } : {}),
    };
  }

  const save = (overrides: Partial<MailboxConfig> = {}, message = "Saved.") =>
    act(async () => {
      const next = await put<MailboxView>(`/admin/projects/${projectId}/mail`, body(overrides));
      setPassword("");
      dirty.current = false;
      adopt(next);
    }, message);

  const status = view?.status;
  const test = status?.last_test;

  return (
    <>
      <Lede>Give the CEO an email address, so it can send a deliverable to someone and hear back.</Lede>
      <Why>
        Mail goes out through this project&apos;s own SMTP server and replies are read from its own
        IMAP inbox — never the platform&apos;s alert mail. Only the CEO writes, and only to the
        addresses listed below. A reply reaches the CEO only if it comes from a listed address,
        your mail server vouches for the sender, and it answers an email the project sent. The
        CEO treats it as feedback, never as instructions.
      </Why>

      {view === null ? <Skeleton rows={3} /> : !view.configured ? (
        <p className="help"><span className="badge warn">not set up</span> fill in the mailbox below.</p>
      ) : !view.enabled ? (
        <p className="help">
          <span className="badge warn">{view.missing.length ? "incomplete" : "off"}</span>{" "}
          {view.missing.length ? `still needs ${view.missing.join(", ")}.`
            : "nothing is sent or read. Queued mail fails rather than waiting."}
        </p>
      ) : (
        <p className="help">
          <span className={`badge ${status?.read_error ? "critical" : "good"}`}>
            {status?.read_error ? "inbox unreadable" : "on"}
          </span>{" "}
          {status?.read_error ? status.read_error
            : status?.read_ok_at ? `inbox last read ${fmtWhen(status.read_ok_at)}`
              : "the inbox is read every minute"}
          {status && status.queued > 0 ? ` · ${status.queued} queued` : ""}
          {status && status.failed > 0 ? ` · ${status.failed} undelivered` : ""}
          {status && status.ignored > 0
            ? ` · ${status.ignored} unrelated message(s) left alone` : ""}.
        </p>
      )}

      <div className="row">
        <div>
          <label className="field" htmlFor="mail-address">Project email address</label>
          <input id="mail-address" value={form.address ?? ""} placeholder="ceo@yourcompany.com"
                 onChange={(e) => set("address", e.target.value)} />
        </div>
        <div>
          <label className="field" htmlFor="mail-name">Sender name (optional)</label>
          <input id="mail-name" value={form.display_name ?? ""} placeholder="Acme"
                 onChange={(e) => set("display_name", e.target.value)} />
        </div>
      </div>
      <div className="row">
        <div>
          <label className="field" htmlFor="mail-smtp">SMTP server (sending)</label>
          <input id="mail-smtp" value={form.smtp_host ?? ""} placeholder="smtp.yourcompany.com"
                 onChange={(e) => set("smtp_host", e.target.value)} />
        </div>
        <div className="row">
          <div>
            <label className="field" htmlFor="mail-smtp-port">Port</label>
            <input id="mail-smtp-port" type="number" value={form.smtp_port ?? 587}
                   onChange={(e) => set("smtp_port", Number(e.target.value))} />
          </div>
          <div>
            <label className="field" htmlFor="mail-smtp-sec">Security</label>
            <select id="mail-smtp-sec" value={form.smtp_security ?? "starttls"}
                    onChange={(e) => {
                      const security = e.target.value as "starttls" | "ssl";
                      dirty.current = true;
                      setForm((f) => ({ ...f, smtp_security: security,
                                        smtp_port: security === "ssl" ? 465 : 587 }));
                    }}>
              <option value="starttls">STARTTLS (587)</option>
              <option value="ssl">TLS (465)</option>
            </select>
          </div>
        </div>
      </div>
      <div className="row">
        <div>
          <label className="field" htmlFor="mail-imap">IMAP server (reading)</label>
          <input id="mail-imap" value={form.imap_host ?? ""} placeholder="imap.yourcompany.com"
                 onChange={(e) => set("imap_host", e.target.value)} />
        </div>
        <div className="row">
          <div>
            <label className="field" htmlFor="mail-imap-port">Port</label>
            <input id="mail-imap-port" type="number" value={form.imap_port ?? 993}
                   onChange={(e) => set("imap_port", Number(e.target.value))} />
          </div>
          <div>
            <label className="field" htmlFor="mail-folder">Folder</label>
            <input id="mail-folder" value={form.imap_folder ?? "INBOX"}
                   onChange={(e) => set("imap_folder", e.target.value)} />
          </div>
        </div>
      </div>
      <div className="row">
        <div>
          <label className="field" htmlFor="mail-user">Username</label>
          <input id="mail-user" value={form.username ?? ""} placeholder="usually the address"
                 onChange={(e) => set("username", e.target.value)} />
        </div>
        <div>
          <label className="field" htmlFor="mail-pass">
            Password {view?.password_set ? <span className="help">(stored — blank keeps it)</span> : null}
          </label>
          <SecretInput id="mail-pass" value={password} onChange={setPassword}
                       placeholder={view?.password_set ? "••••••••" : "app password"} />
        </div>
      </div>
      <p className="help">
        Gmail needs an app password (it requires 2-step verification). Microsoft 365 may refuse
        password sign-in for SMTP and IMAP entirely. The password is stored encrypted, and no
        agent is ever given it: the platform sends the CEO&apos;s mail itself.
      </p>

      <label className="field" htmlFor="mail-recipients">
        Who the CEO may email — one address per line
      </label>
      <textarea id="mail-recipients" rows={4} value={recipients}
                placeholder={"editor@client.com\nreview-agent@partner.com"}
                onChange={(e) => { dirty.current = true; setRecipients(e.target.value); }} />
      <p className="help">
        People or other agents. Anyone not listed is refused, and taking an address off the list
        ends the CEO&apos;s conversation with it. Replies are accepted from these addresses only.
      </p>

      <label className="check-line">
        <input type="checkbox" checked={form.verify_sender !== false}
               onChange={(e) => set("verify_sender", e.target.checked)} />
        Only accept replies your mail server has verified the sender of (DMARC, DKIM or SPF)
      </label>
      {form.verify_sender === false ? (
        <p className="help">
          <span className="badge warn">off</span> anyone who types a listed address into a From
          line will be read as that person. Turn this off only for a server that records no
          Authentication-Results.
        </p>
      ) : null}

      {test ? (
        <p className="help">
          Tested {fmtWhen(test.at)}:{" "}
          <span className={`badge ${test.smtp.ok ? "good" : "critical"}`}>SMTP</span>{" "}
          {test.smtp.ok ? "" : test.smtp.error}{" "}
          <span className={`badge ${test.imap.ok ? "good" : "critical"}`}>IMAP</span>{" "}
          {test.imap.ok ? "" : test.imap.error}
        </p>
      ) : null}

      <div className="row">
        <button className="primary" disabled={busy} onClick={() => save()}>
          Save mailbox
        </button>
        {view?.configured ? (
          <button
            disabled={busy || testing}
            onClick={async () => {
              setTesting(true);
              try {
                await act(() => post(`/admin/projects/${projectId}/mail/test`), (r) =>
                  (r as { ok: boolean }).ok
                    ? "Signed in to both servers. Nothing was sent."
                    : "The test failed — the reason is next to each server above.");
              } finally {
                setTesting(false);
                load();
              }
            }}
          >
            {testing ? "Testing…" : "Test connection"}
          </button>
        ) : null}
        {view?.configured && view.enabled ? (
          <button disabled={busy} onClick={() => save({ enabled: false },
            "The mailbox is off. Nothing is sent or read until you turn it back on.")}>
            Turn off
          </button>
        ) : view?.configured && !view.missing.length ? (
          <button disabled={busy} onClick={() => save({ enabled: true }, "The mailbox is on.")}>
            Turn on
          </button>
        ) : null}
        {view?.configured ? (
          <button
            className="danger"
            disabled={busy}
            onClick={() => {
              if (!confirm("Remove this project's mailbox? Its password is deleted; the threads "
                           + "stay on the record.")) return;
              act(async () => {
                await del(`/admin/projects/${projectId}/mail`);
                load();
              }, "Mailbox removed.");
            }}
          >
            Remove
          </button>
        ) : null}
      </div>
      {status ? (
        <p className="help">
          Limits: {status.limits.sends_per_day} emails a day; a thread freezes at{" "}
          {status.limits.messages_per_thread} messages until you reopen it.
        </p>
      ) : null}
    </>
  );
}

/** Details › Activity › Email: every thread the CEO has had, read-only. */
export function MailThreads({ projectId }: { projectId: string }) {
  const { data, error, reload } = usePoll(
    () => get<{ threads: MailThreadRow[] }>(`/observe/projects/${projectId}/mail`), 20000,
    [projectId]);
  const [open, setOpen] = useState<string | null>(null);
  const [messages, setMessages] = useState<MailMessageRow[] | null>(null);
  const [problem, setProblem] = useState<string | null>(null);

  useEffect(() => {
    setMessages(null);
    if (!open) return;
    get<{ messages: MailMessageRow[] }>(`/observe/projects/${projectId}/mail/${open}`)
      .then((t) => setMessages(t.messages)).catch((e) => setProblem((e as Error).message));
  }, [open, projectId]);

  if (error) return <div className="card"><p className="error">{error}</p></div>;
  if (!data) return <div className="card"><Skeleton rows={4} /></div>;
  if (data.threads.length === 0) {
    return (
      <div className="card" style={{ marginTop: "var(--s3)" }}>
        <p className="subtitle">
          The CEO has not emailed anyone. Give the project a mailbox in Settings → Email and
          list who it may write to.
        </p>
      </div>
    );
  }
  return (
    <div className="card" style={{ marginTop: "var(--s3)" }}>
      {problem ? <p className="error">{problem}</p> : null}
      <table>
        <thead>
          <tr><th>With</th><th>Subject</th><th>Messages</th><th>Last</th><th /></tr>
        </thead>
        <tbody>
          {data.threads.map((t) => (
            <tr key={t.id} className={open === t.id ? "selected" : ""}
                onClick={() => setOpen(open === t.id ? null : t.id)} style={{ cursor: "pointer" }}>
              <td>{t.counterpart}</td>
              <td>
                {t.subject}{" "}
                {t.frozen ? <span className="badge warn">frozen</span> : null}
                {t.unread ? <span className="badge">{t.unread} unread by the CEO</span> : null}
              </td>
              <td>{t.messages}</td>
              <td>{fmtWhen(t.last_at)}</td>
              <td>
                {t.frozen ? (
                  <button onClick={async (e) => {
                    e.stopPropagation();
                    try {
                      await post(`/admin/projects/${projectId}/mail/threads/${t.id}/reopen`);
                      reload();
                    } catch (err) {
                      setProblem((err as Error).message);
                    }
                  }}>Reopen</button>
                ) : null}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {open && messages ? (
        <div style={{ marginTop: "var(--s3)" }}>
          {messages.map((m) => (
            <div key={m.id} className="card" style={{ marginBottom: 8 }}>
              <p className="subtitle">
                <b>{m.direction === "out" ? "CEO" : m.from}</b> → {m.direction === "out" ? m.to : "CEO"}
                {" · "}{fmtWhen(m.at)}{" · "}
                <span className={`badge ${m.status === "failed" ? "critical"
                  : m.status === "held" || m.status === "queued" ? "warn" : "good"}`}>{m.status}</span>
                {m.attachment?.name ? <> · attached <b>{m.attachment.name}</b></> : null}
              </p>
              <pre style={{ whiteSpace: "pre-wrap" }}>{m.body}</pre>
              {m.attachments?.length ? (
                <p className="help">
                  Attached (not imported): {m.attachments.map((a) => a.name).join(", ")}
                </p>
              ) : null}
              {m.error ? <p className="error">{m.error}</p> : null}
            </div>
          ))}
        </div>
      ) : null}
    </div>
  );
}
