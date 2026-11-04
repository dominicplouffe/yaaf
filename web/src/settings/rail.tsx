// The Settings rail: one entry per section, each carrying its state. On a
// phone the same list is the page itself — an index with a state per row —
// and choosing a row opens that section with a way back.

import { Icon } from "../ui/Icon";
import { SettingsData } from "./data";
import { railNote, SECTIONS, SectionKey } from "./state";

export function SettingsRail({ data, section, email, onPick, index }: {
  data: SettingsData;
  section: SectionKey | null;
  email: string;
  onPick: (key: SectionKey) => void;
  /** The phone's version: the list is the page, with a chevron per row. */
  index?: boolean;
}) {
  const visible = SECTIONS.filter((s) => data.admin || !s.admin);
  const rows: React.ReactNode[] = [];
  let group: React.ReactNode[] = [];
  const flush = (key: string) => {
    if (!group.length) return;
    rows.push(index ? <div key={key} className="set-rail-list">{group}</div> : group);
    group = [];
  };
  visible.forEach((item, i) => {
    if ("group" in item) {
      flush(`g${i}`);
      rows.push(<div className="set-rail-grp" key={item.group}>{item.group}</div>);
      return;
    }
    // On the phone, Overview is the card above the list, not a row in it.
    if (index && item.key === "overview") return;
    const note = railNote(item.key, data, email);
    group.push(
      <button type="button" key={item.key} className={`set-rail-item${section === item.key ? " on" : ""}`}
        aria-current={section === item.key ? "page" : undefined} onClick={() => onPick(item.key)}>
        <span className="l">{item.label}</span>
        {note.count ? <span className="cnt" aria-label={`${note.count} need you`}>{note.count}</span> : <span />}
        <span className={`s ${note.tone === "critical" || note.tone === "warn" ? note.tone : ""}`}>
          {note.tone && <span className={`dot ${note.tone}`} aria-hidden="true" />}
          <span>{note.text}</span>
        </span>
        <span className="chv" aria-hidden="true"><Icon name="right" size={14} /></span>
      </button>);
  });
  flush("end");
  return <nav className="set-rail" aria-label="Settings sections">{rows}</nav>;
}
