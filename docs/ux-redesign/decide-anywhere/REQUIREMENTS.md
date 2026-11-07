# Decide anywhere — Phase A requirements

Phase A builds the foundation every later surface stands on: one server-side object for "this
needs you", one way to commit it, and the rule that a clock may not decide for a person who was
never reached. It adds no new surface. Design and rationale: [`README.md`](README.md).

Each requirement names how it is enforced and the test that attacks it. "Must" is a refusal or an
automatic state change, per house rule 1; nothing here is enforced by a prompt.

---

## A1 — The item producer

| ID | Requirement | Acceptance |
|---|---|---|
| A1.1 | `yaaf/server/needs_you.py` produces `yaaf.needs_you/1` items for one project from the database alone, for eight families: approval, ask, scope, commitment, verdict, signoff, bet, frozen. | `test_needs_you_items.py` builds each family and reads it back. |
| A1.2 | Item keys are the Inbox's keys, spelled once on the server (`notify.*_item` helpers). | The web Inbox selects rows by the server's key; `test_inbox_links.py` still passes. |
| A1.3 | For every project, the number of items equals `policy.needs_you(...)["total"]`. One definition of "needs you", whoever counts. | A test fills every family and compares the two. |
| A1.4 | Every item says what silence does (`silence`) and, where a clock exists, when (`deadline`). An approval's silence follows the project's own `approval_timeout_decision`. | Test with an approve-on-timeout project. |
| A1.5 | Items carry `options`; `commit` is true only for families whose options are committed through A3 (approval, ask, scope, commitment, verdict, signoff). Bets and frozen threads carry a `path` and no committable options. | Test per family. |
| A1.6 | `GET /observe/needs-you` returns the signed-in person's items across the running projects they may open, ranked by tier then deadline; `?project_id=` narrows to one project of any status. A person sees nothing from a project they have no standing on. | Test: a stranger's project's items never appear; observer sees them read-only (`decider: false`). |
| A1.7 | `/observe/desk` carries each project's items as `needs_you_items`, from the same producer. | Desk and endpoint agree in a test. |

## A2 — Reach receipts

| ID | Requirement | Acceptance |
|---|---|---|
| A2.1 | A `reach_receipts` row records that an item reached a person: `kind` *delivered* or *seen*, the `channel`, the person, when. Migration `0066`. | Model + migration test (`test_migrations` chain). |
| A2.2 | `chairman_notify(..., item_key=…)` records *delivered* when, and only when, it dispatched to a configured channel: an email inside the ration (`email`), or the webhook (`webhook`). Nothing configured → no receipt. A ration-held email is not a delivery. | Tests with the wire faked, with nothing configured, and with the ration spent. |
| A2.3 | Every notice about a specific item carries its key: approval filed and expiring, question filed and chased, delivery finished, scope and a date waiting. Approval and question notices already carry their row in `data`, and `chairman_notify` reads the key from it, so no call site can forget; the others pass `item_key`. | `test_needs_you_items.py`: an approval and a question, mailed, each record a delivery. |
| A2.4 | Reading the list as a decider (chairman or co-chair) records *seen* for each item listed, once per person, item and channel; the channel is the door (`dashboard`, `mcp`, `cli`). An observer's read and the instance admin's read of a project they do not chair record nothing. | Tests per door and per role. |
| A2.5 | A receipt is never a decision and never changes an item's state. | Asserted alongside A2.4. |

## A3 — Preview and commit

| ID | Requirement | Acceptance |
|---|---|---|
| A3.1 | `POST /admin/projects/{id}/needs-you/preview` `{key, option, note}` returns what would happen (`about_to`: headline, option label, consequence, irreversible) and a single-use ticket. It changes nothing. STEER. | Test: state unchanged after preview. |
| A3.2 | `POST /admin/projects/{id}/needs-you/commit` `{key, option, note, ticket}` performs the option through the existing verb — `decide_approval`, a boardroom reply, `accept_requirement`, `accept_commitment`/`decline_commitment`, `requirement_verdict`, `complete_project` — so every rule those verbs enforce still applies. | Test per family that the underlying state changed exactly as the dashboard's own button would. |
| A3.3 | A ticket is refused if it is unknown, someone else's, for another project, already spent, older than 5 minutes, or bound to a different key, option, note or item content. | `test_needs_you_commit.py` attacks each. |
| A3.4 | Committing an item that is no longer waiting answers 409 with what happened to it, never a second decision. | Test: decide on the dashboard, then commit an old ticket. |
| A3.5 | An option with `needs_note` is refused without one (a "not met" verdict, an answer in your own words). | Test. |
| A3.6 | The commit is attributed to the person through the door they came in by (the request's bound actor), and audited as `needs_you.commit` with the key and option. An agent's run token cannot reach either route. | Test reads the audit stamp; agent token gets 401. |
| A3.7 | Observers are refused (403); a stranger gets 404. | Test. |

The dashboard keeps its existing buttons and verbs in Phase A: they are the same functions, and
the e2e suite covers them. Preview/commit exists for the surfaces in Phases C and D.

## A4 — Silence counts only if you were told

| ID | Requirement | Acceptance |
|---|---|---|
| A4.1 | An item is **reached** when a receipt exists from a decider at least `lead` before its deadline, where `lead = min(6h, 25% of the window from filing to deadline)`. | Unit test of the boundary. |
| A4.2 | When a pending approval or open question reaches its deadline unreached and has not been extended, the deadline moves to now + 24h, once. The chairman is sent an ALARM, the boardroom gets a line, and the filing agent is told it waits longer and why. Audited `needs_you.extended`. | `test_silence_needs_reach.py`. |
| A4.3 | At an extended deadline, still unreached: an approval is **declined**, even on a project whose rules approve on silence; a question lapses to the CEO's stated default as usual. Each notice says it was never reached. | Tests for both timeout settings. |
| A4.4 | A reached item expires exactly as before. | Existing expiry tests pass with a receipt in their setup. |
| A4.5 | A finished delivery closes itself only if its sign-off was reached; otherwise it waits, spending nothing, for the chairman's Complete. | Test beside `test_delivery_closes_itself.py`. |
| A4.6 | Extension happens at most once per item. | Test: two passes, one extension. |

## A5 — Time the box was down does not count

| ID | Requirement | Acceptance |
|---|---|---|
| A5.1 | The janitor stores when it last swept (`PlatformSetting` `janitor.last_sweep_at`). | Test. |
| A5.2 | When the gap since the last sweep exceeds `max(10 × janitor_poll_s, 10 min)`, every pending approval and open question on a running project has its deadline moved later by the gap, before any expiry runs in that sweep. One `clock.paused` audit row per project names the gap. | Test sets the stored sweep time back a day and checks deadlines moved and nothing expired. |
| A5.3 | No gap, or the first sweep ever, moves nothing. | Test. |

## A6 — Local decision metrics

| ID | Requirement | Acceptance |
|---|---|---|
| A6.1 | `GET /me/decision-stats?days=30` reports, for the projects the person decides on: decisions made by the person (approvals and questions), median minutes from filing to decision, decisions taken by silence by family, clocks extended because nobody was reached, and items resolved without ever reaching anyone. Read from rows; nothing is sent anywhere. | Test with a seeded mix. |
| A6.2 | Settings → Account shows those numbers in one card ("Your decisions"). | `npm run build`; the settings e2e opens Account. |

## A7 — The Inbox renders server items

| ID | Requirement | Acceptance |
|---|---|---|
| A7.1 | `web/src/desk.tsx` builds the eight decision families from `needs_you_items`, not from the raw payload; the watching rows (bet deadlines, drought, budget, no proof) stay where they are. | `npm run build`; Inbox e2e passes. |
| A7.2 | Keys, tiers, snooze and the badge count are unchanged. | e2e and `needsYouCount` agree with the server's count. |
| A7.3 | Each card states when silence was extended because nobody was reached. | Visible on an extended item. |

---

## Footprint (CLAUDE.md: "match the sibling's footprint")

The nearest siblings are `policy.needs_you` (the count), `Snooze` (a per-person record keyed by
Inbox item) and `McpConfirmation` (a preview ticket). Every place they reach, and what Phase A does
there:

| Place | Phase A |
|---|---|
| `/observe/desk` | Carries `needs_you_items` (A1.7) and records *seen* (A2.4). |
| Web Inbox, Home, Overview, sidebar badge | Render server items (A7). |
| MCP | The `desk` tool reads `/observe/desk`, so a decider's read through Claude records *seen* as `mcp`. It **drops `needs_you_items`** from what it returns: every headline, case, option and default on them is agent text, and MCP wraps agent text field by field (rule 5); the wrapped members carry the same decisions (`test_mcp_untrusted.py`). The two commit routes are withheld from MCP in `mcp/surface.py`: a model client decides through its own steer tools behind `mcp/confirm.py`. A wrapped needs-you tool is Phase F's. |
| CLI | `yaaf status` and `yaaf briefing` count with `policy.needs_you`, which A1.3 holds equal to the items. A `yaaf needs-you` listing is deferred: it would be a third renderer before the second ships. |
| Daily brief | The extension and the unreached resolution are notices on the record and lines in the boardroom, which the brief already reads. No new section in A. |
| Agenda | Reads `expires_at` / `answer_by` from rows, so extensions and pauses move the agenda by themselves. |
| Change stamp / audit | `needs_you.commit`, `needs_you.extended`, `needs_you.unreached`, `clock.paused`. |
| Agents | The filing agent gets a notice on extension and on an unreached resolution. Golden evals re-recorded only if a scripted scenario reaches this path; the PR says so. |
| Docs | `docs/GOVERNANCE.md` (the silence rule), `docs/DASHBOARD.md` (Account card), `docs/AUTONOMY.md` (what silence decides), this folder. |
| Snooze | Unchanged; a snooze still ends two hours before the (possibly moved) deadline. |

## Out of scope for A

New surfaces (push, Slack cards, the phone layout), device tokens, `yaaf setup` changes, the
attention engine, co-chair delivery, and clocks other than approvals, questions and sign-off
(bets, audit samples and reviews keep their behaviour). The dashboard's own buttons keep their
endpoints.

## Security notes

* Every item's `headline`, `summary`, `case`, `recommendation`, option labels and (for a question)
  `silence` contain text an agent wrote. A renderer that hands an item to a model must wrap those
  fields as untrusted, as the MCP surface does; a renderer for a person shows them as quoted.
* A receipt is not a credential and not a decision; it only lets a clock count.
* Commit tickets are stored as hashes, single-use, five minutes long, and bound to the person.

## Open questions

1. Is a webhook dispatch a delivery? A receiver can be anything, including a log sink. Phase A
   counts it, because it is the integration people point at ntfy or a pager today; revisit if it
   lets silence count too easily.
2. Should a decider's *seen* from a background tab count? Phase A says yes — it was on their
   screen — and the metric for "committed without opening" waits for surfaces that can tell the
   difference.
