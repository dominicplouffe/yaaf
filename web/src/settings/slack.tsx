// Settings → Slack: what works now, and what the next token unlocks.
//
// It was one form of six fields whose labels carried their own state and
// instructions ("App token (xapp-…) — chat from Slack, no public URL — blank
// keeps current"), a green "connected" for a token that was only saved, one
// button that saved secrets and channel preferences together, and the steps to
// the best thing it does — chatting with the CEO from Slack — folded last.
//
// Now: a three-step ladder of what the saved tokens make possible, each token
// as a row that says Saved or Not set, the channels with their names drawn as
// they will be made, and "Not tested" until a test says otherwise.

import { useEffect, useState } from "react";
import { del, post, put } from "../api";
import { ConfirmModal } from "../ui";
import { Seg } from "../ui/kit";
import { Icon } from "../ui/Icon";
import { Why } from "../settingsCopy";
import { SlackSettings } from "./data";
import { Flash, Group, SaveBar, SecretRow, SectionHead, TestSeal, Toggle } from "./kit";

function Step({ done, n, title, children }: { done: boolean; n: number; title: string; children: React.ReactNode }) {
  return <div className="ladder-step">
    <span className={`ladder-node ${done ? "ok" : "todo"}`} aria-hidden="true">
      {done ? <Icon name="check" size={13} strokeWidth={2.6} /> : n}</span>
    <span className="t">{title}<span className="sr-only">{done ? " — working" : " — not yet"}</span></span>
    <div className="d">{children}</div>
  </div>;
}

export function SlackSection({ settings, onChanged, slug }: {
  settings: SlackSettings;
  onChanged: () => void;
  /** A real project's slug, so the room names drawn are ones you will see. */
  slug?: string;
}) {
  const [bot, setBot] = useState("");
  const [signing, setSigning] = useState("");
  const [app, setApp] = useState("");
  const [open, setOpen] = useState<Record<string, boolean>>({});
  const [member, setMember] = useState(settings.chairman_slack_user);
  const [prefix, setPrefix] = useState(settings.channel_prefix);
  const [visibility, setVisibility] = useState(settings.channel_visibility);
  const [auto, setAuto] = useState(settings.auto_create_channels);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<{ ok: boolean; text: string } | null>(null);
  const [leaving, setLeaving] = useState(false);

  useEffect(() => {
    setMember(settings.chairman_slack_user);
    setPrefix(settings.channel_prefix);
    setVisibility(settings.channel_visibility);
    setAuto(settings.auto_create_channels);
  }, [settings]);

  const connected = settings.bot_token_configured;
  const dirty = !!bot.trim() || !!signing.trim() || !!app.trim()
    || member.trim() !== settings.chairman_slack_user || prefix.trim() !== settings.channel_prefix
    || visibility !== settings.channel_visibility || auto !== settings.auto_create_channels;
  const base = prefix.trim() || slug || "{project slug}";
  const chatIn = settings.app_token_configured && !!settings.chairman_slack_user;

  async function save() {
    setBusy(true); setNote(null);
    try {
      await put("/admin/settings/slack", {
        ...(bot.trim() ? { bot_token: bot.trim() } : {}),
        ...(signing.trim() ? { signing_secret: signing.trim() } : {}),
        ...(app.trim() ? { app_token: app.trim() } : {}),
        chairman_slack_user: member.trim(), channel_prefix: prefix.trim(),
        channel_visibility: visibility, auto_create_channels: auto,
      });
      setBot(""); setSigning(""); setApp(""); setOpen({});
      setNote({ ok: true, text: "Saved. Every project that has not overridden Slack inherits it now." });
      onChanged();
    } catch (e) { setNote({ ok: false, text: (e as Error).message }); } finally { setBusy(false); }
  }

  async function test() {
    setBusy(true); setNote(null);
    try {
      const r = await post<{ ok: boolean; detail: string }>("/admin/settings/slack/test");
      setNote({ ok: r.ok, text: r.detail });
      onChanged();
    } catch (e) { setNote({ ok: false, text: (e as Error).message }); } finally { setBusy(false); }
  }

  const row = (key: string) => ({ open: !!open[key], setOpen: (v: boolean) => setOpen((o) => ({ ...o, [key]: v })) });

  return <>
    <SectionHead title="Slack"
      lede="One workspace for every project. Each project gets its own rooms, and can override any of this in its own Settings."
      actions={connected && <><TestSeal test={settings.last_test} />
        <button type="button" disabled={busy} onClick={test}>Test connection</button></>}>
      {!connected && <span>You need a Slack app with the <code>chat:write</code>, <code>channels:manage</code> and{" "}
        <code>groups:write</code> bot scopes, installed to your workspace.</span>}
    </SectionHead>
    <Flash note={note} />

    <div className="set-card ladder" style={{ marginBottom: "var(--s6)" }}>
      <Step done={connected} n={1} title="Mirror to Slack">
        {connected ? "Boardrooms and team rooms get every message." : "Needs the bot token."}</Step>
      <Step done={!!settings.chairman_slack_user} n={2} title="Invite you to private rooms">
        {settings.chairman_slack_user
          ? <>As member <code>{settings.chairman_slack_user}</code>, the only Slack user yaaf trusts.</>
          : "Needs your Slack member ID."}</Step>
      <Step done={chatIn} n={3} title="Chat with the CEO from Slack">
        {chatIn ? "Your messages in a boardroom reach its CEO, and the replies come back."
          : <>Needs an app token for Socket Mode{settings.chairman_slack_user ? "" : " and your member ID"}. No public URL.
            <Why label="How">In the Slack app, turn on <b>Socket Mode</b> (Settings → Socket Mode), make an
              app-level token with <code>connections:write</code>, add it below, and subscribe the bot to{" "}
              <code>message.channels</code>.</Why></>}</Step>
    </div>

    <Group title="Tokens">
      <div className="set-card">
        <SecretRow id="slack-bot" label="Bot token" hint="xoxb-…, from OAuth & Permissions"
          saved={settings.bot_token_configured} value={bot} onChange={setBot} placeholder="xoxb-…"
          disabled={busy} {...row("bot")} />
        <SecretRow id="slack-signing" label="Signing secret" hint="From Basic Information"
          saved={settings.signing_secret_configured} value={signing} onChange={setSigning}
          disabled={busy} {...row("signing")} />
        <SecretRow id="slack-app" label="App token" hint="xapp-…, from App-Level Tokens"
          saved={settings.app_token_configured} unlocks="unlocks chat from Slack" value={app}
          onChange={setApp} placeholder="xapp-…" disabled={busy} {...row("app")} />
        <div className="set-row">
          <label className="set-row-lab" htmlFor="slack-member">Your member ID
            <span className="set-row-hint">U…, in your Slack profile</span></label>
          <div className="set-row-val"><input id="slack-member" value={member} disabled={busy}
            placeholder="Empty: yaaf only mirrors" onChange={(e) => setMember(e.target.value)} /></div>
          <div className="set-row-act" />
        </div>
      </div>
    </Group>

    <Group title="Channels">
      <div className="set-card pad set-fields">
        <div className="field-wrap">
          <label className="field" htmlFor="slack-prefix">Name prefix</label>
          <input id="slack-prefix" value={prefix} disabled={busy} placeholder="The project’s slug"
            onChange={(e) => setPrefix(e.target.value)} />
          <div className="room-names" aria-label="Rooms are named">
            <span className="sg-fact">#{base}-boardroom</span><span className="sg-fact">#{base}-leadership</span>
            <span className="sg-fact">#{base}-&lt;team&gt;</span></div>
        </div>
        <div className="field-wrap">
          <span className="field">New rooms are</span>
          <Seg value={visibility} onPick={setVisibility}
            options={[{ key: "private", label: "Private" }, { key: "public", label: "Public" }]} />
          <span className="help">{visibility === "private"
            ? "Invisible until you are invited, so yaaf invites your member ID to each one it makes."
            : "Anyone in the workspace can find and join them."}</span>
        </div>
        <div className="full set-sep">
          <Toggle id="slack-auto" checked={auto} onChange={setAuto} disabled={busy}
            label="Make a project’s boardroom when the project is created"
            sub="Other rooms wait for “Create missing channels” in that project. A name Slack already has is adopted, never duplicated." />
        </div>
      </div>
    </Group>

    <SaveBar dirty={dirty} busy={busy} onSave={save} label={connected ? "Save changes" : "Connect Slack"}
      danger={connected && <button type="button" className="quiet-danger" disabled={busy}
        onClick={() => setLeaving(true)}>Disconnect Slack…</button>} />

    {leaving && <ConfirmModal title="Disconnect Slack?" confirmLabel="Disconnect" danger busy={busy}
      onCancel={() => setLeaving(false)}
      onConfirm={async () => {
        setBusy(true);
        try { await del("/admin/settings/slack"); setNote({ ok: true, text: "Slack is disconnected. Projects with their own tokens are unaffected." }); onChanged(); }
        catch (e) { setNote({ ok: false, text: (e as Error).message }); }
        finally { setBusy(false); setLeaving(false); }
      }}>
      <p>Projects that inherit this connection stop mirroring. Nothing is deleted in Slack and no message
        is lost: yaaf keeps its own copy of every one. Projects with their own tokens are unaffected.</p>
    </ConfirmModal>}
  </>;
}
