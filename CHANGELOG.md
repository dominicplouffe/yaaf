# Changelog

Release notes for [yaaf](https://github.com/dominicplouffe/yaaf). One release per week, written in
the [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) form and numbered by the chairman's
rule.

A week is seven days counted from Thursday 1 October 2026 — 1–7 October, 8–14 October, and so on —
and each release is dated by the last day of its week. The number a release gets is arithmetic, not
judgement: six or more changes bump the minor version and reset the patch, five or fewer bump the
patch, and a week with no changes gets no release and the number does not move.

Every counted change has exactly one entry below, and the pull request number or commit SHA the
count was made from is the link at the end of that entry. Count the entries in a section and you
have that week's change count, and therefore its version. [Notes on method](#notes-on-method) at the
end says how the counting is done and where the week boundary falls.

| Release | Week | Changes | In one line |
|---|---|---|---|
| **0.5.0** | 29 Oct – 4 Nov 2026 | 23 | One honest meter for what a run costs, and runs that stop being cut short |
| **0.4.0** | 22–28 Oct 2026 | 34 | Any model provider, one provider chosen for the install, and an inbox whose buttons do something |
| **0.3.0** | 15–21 Oct 2026 | 7 | A confirmed brief holds a change of direction, and the boardroom shows what you have not read |
| **0.2.0** | 8–14 Oct 2026 | 11 | People sign in, the admin token goes, any harness runs a seat, and the Deliverables tab stops falling over |
| **0.1.1** | 1–7 Oct 2026 | 4 | Cheaper Opus, a connector that survives an address change, and a CEO that can open a task |
| **0.1.0** | — | — | The starting point: the last commit before 1 October 2026 |

Releases are listed newest first.

## [0.5.0] - 2026-11-04

**One thing worth deciding, nothing you must do.** A project keeps the run limits it was created
with — on the default cards that is a $2 cap on what one run may spend and a 30-minute run timeout;
the new $20 and 90 minutes apply to projects created from now on. If your runs are still being cut
off mid-task, raise **Most one run may spend** and **Run timeout (seconds)** in that project's
Settings. Nothing else needs you: the update script applies the database changes, and no new
credential is required.

### Changed

- **Every provider now meters against the same budget.** A project's monthly cap is kept in tokens
  for every provider, so a DeepSeek key and a Claude subscription paying seats on the same project
  count against one ceiling, with dollars shown as the cost. A project that meters in dollars keeps
  its hard stop in dollars. (The conversion of dollar budgets into token caps described here was
  replaced by the next entry before the release shipped.) ([#81](https://github.com/dominicplouffe/yaaf/pull/81))
- **Mixed payers are billed honestly, and the two units are never converted.** The credential that
  paid a run now decides whether dollars moved: an API key books its real bill, a subscription books
  no dollars, and on a dollar budget a subscription seat is counted at its own catalog price rather
  than a guessed exchange rate. This replaces #81's conversion of dollar budgets into token caps,
  which was a guess — cache reads swing the tokens-per-dollar ratio about tenfold. Each project keeps
  the unit it already used, and mixing them in either direction is allowed. ([#82](https://github.com/dominicplouffe/yaaf/pull/82))
- **The CEO's messages to you are refused when they would need reading twice.** A boardroom post
  over 150 words, or with a sentence over 30, is refused with what to fix, and the chat lane
  rewrites once and sends the rewrite rather than leaving you unanswered. This follows months of
  "plain English, three sentences" being met by three run-on sentences; one message ran 206 words in
  two. ([#87](https://github.com/dominicplouffe/yaaf/pull/87))
- **The CEO's review is held to the same length limits as the boardroom.** ([#88](https://github.com/dominicplouffe/yaaf/pull/88))
- **The review is a status meeting: what was done first, then the CEO's plan held to account.** It
  opens on what actually closed, from the record; the CEO files a status report (what it bought,
  what went wrong, what is next with each item serving something open, and the ask) where every
  section can be refused; the next meeting opens on that list marked item by item, and claiming
  "done" against an open requirement is refused. The levers sit under the ask with what each moved,
  and you can say something back that the CEO answers. On an Operate rhythm the meeting happens at
  the roll. ([#89](https://github.com/dominicplouffe/yaaf/pull/89))
- **What needs you is now one number, and every screen says where the work stands.** Home is a
  portfolio: the decisions waiting on you with their verbs, then one comparable row per project —
  state, a sentence, outcomes, the next date, and money against both the plan and the wall. A
  project opens on its decisions, then outcomes against the promise, what changed, what is next,
  money, and results. A finished delivery appears in "what needs you" as a sign-off. Approvals now
  carry the CEO's one-sentence recommendation, stored rather than guessed from the case.
  ([#90](https://github.com/dominicplouffe/yaaf/pull/90))
- **DeepSeek's V4 models are shipped, and a retired name that still answers is named as retiring.**
  The health checklist used to tell you a seat on a retired DeepSeek model "would be refused by the
  provider" while a check on the same key had the model answer every call. DeepSeek retired
  `deepseek-chat` and `deepseek-reasoner` on 24 July in favour of the V4 lineup; a name dropped from
  the list but still answering is a retired alias not yet switched off, and the day it is, every
  seat on it stops at once. The warning stays and the claim is now the true one. ([#92](https://github.com/dominicplouffe/yaaf/pull/92))
- **The cap on what one run may spend is $20, not $2.** At $2 the cap stopped 1,224 runs across 35
  projects that were working — 31 of one project's 59 dead runs. Over the previous 45 days an
  ordinary run cost cents, 99% of healthy runs finished under about $7, and the largest about $13.
  The Full send card goes to $50. Projects you already have keep their own value; change it in the
  project's Settings. ([#96](https://github.com/dominicplouffe/yaaf/pull/96))
- **The run timeout is 90 minutes, not 30.** The 30-minute line killed 338 runs across 12 projects,
  90 of them in one week, and of the timed-out runs that were retried 142 then finished, typically
  in seven more minutes. Healthy runs have a median under a minute and a 99th percentile of 23. Full
  send goes from 60 minutes to 3 hours. The $20 per-run cap is what stops a run that is looping.
  ([#97](https://github.com/dominicplouffe/yaaf/pull/97))
- **Models and prices stay current, and long prompts are metered right.** Upkeep is on by default: a
  provider's model list is priced and described as the key is read, the picker shows price, context
  window and whether a model reasons, and non-chat models are hidden. Prices now carry their
  long-prompt tiers, so a call past the tier's line pays the higher rate instead of half of what it
  spent — this was Grok, Gemini Pro and GPT-6 being billed at their base rate. Empty model classes
  on providers that sell their own models are filled from the key's list by price group, newest
  first, and a filled class moves only when you press. ([#102](https://github.com/dominicplouffe/yaaf/pull/102))
- **Settings is rebuilt: it opens on what needs you, and a provider is green only once a key has
  answered.** The Overview ranks what needs you by what it stops, with the fix beside each, and
  shows what each class runs on per provider and when that key last answered. A provider reads
  Ready only after a key has actually answered — a test, its model list, a graded check, or a run it
  paid — since its value last changed; otherwise it says Untested, or Failing when the provider
  refused it. `yaaf doctor` names a refused key. ([#103](https://github.com/dominicplouffe/yaaf/pull/103))

### Fixed

- **A seat move meets every problem before Save, and can set up web search in place.** The seat check
  used to stop at its first problem, so moving a seat onto DeepSeek met a credential, then a price,
  then web search, one refusal per click. It now reports every problem at once with its kind, and
  offers the doors: a web-search backend and its key, tested with one real query, then the seat
  saves. ([#83](https://github.com/dominicplouffe/yaaf/pull/83))
- **A sandbox imports its own files on its own Python, or the build stops.** One string split across
  an f-string field was accepted by Python 3.12 but not by the 3.11 the sandbox image runs, so every
  seat on yaaf's own loop died at import and its container was reported as stopped. The image build
  and a CI job now import every shipped file on the base image's interpreter. ([#84](https://github.com/dominicplouffe/yaaf/pull/84))
- **A deliverable in the chat opens the file, and the project card opens a switcher.** The
  "Deliverable published" link used to send you to the Roadmap, because the redirect read the parent
  route's path; old `/tasks`, `/spend` and `/reviews` links are fixed by the same change. A
  deliverable now opens in the reader over the conversation, and the sidebar's project card opens a
  project switcher instead of the command palette with "project" typed in.
  ([#85](https://github.com/dominicplouffe/yaaf/pull/85))
- **The boardroom answers what you just asked.** A CEO on a summarising provider kept answering the
  first question of the session — three times over one day — because each long conversation was
  compacted onto the transcript's first user turn and the newest message survived only inside the
  summary. The summary is now written on the message being answered, and a conversation between
  turns is never summarised: past half the model's window it re-primes from the boardroom, which is
  its record. ([#86](https://github.com/dominicplouffe/yaaf/pull/86))
- **A Founder draft whose team has no employees no longer breaks the review form.** The form called
  `.join()` on a missing list and stopped halfway through, so no roles were picked; drafts already
  saved in that shape load too. ([#91](https://github.com/dominicplouffe/yaaf/pull/91))
- **A report owed every morning is judged every morning, and the scorecard says what it counts.** One
  weekly review said "0 of 4 met, 1 carried forward" for a report that had landed before its 08:00
  deadline every day: three of the four were cut versions of the same report still riding in the
  denominator, and the fourth was judged only when all its work closed — which a daily pipeline
  never does. Cut requirements no longer roll forward, a daily check runs daily, and the sentence
  names the thing it counted. ([#93](https://github.com/dominicplouffe/yaaf/pull/93))
- **The review page stops saying three things that are not true.** Meetings that opened before the
  record existed no longer headline "No cycle closed this period" over seven cycles that closed;
  "Met since last" counts requirements rather than events, so an Operate project no longer shows one
  met next to "0 met" (frozen meetings are re-counted on upgrade); and a week the platform never
  checked says "closed without being checked" instead of "closed missed". ([#94](https://github.com/dominicplouffe/yaaf/pull/94))
- **Cutting an obligation no longer raises a "missed" flag.** Cut is a decision, not a miss: the
  weekly roll wrote a cycle result for cut standing requirements and each one raised "owed every
  cycle and cycle N closed without it" — 21 flags in seven weeks for three cut versions of one
  report. ([#95](https://github.com/dominicplouffe/yaaf/pull/95))
- **A brief you confirm after the last requirement came true reopens the project, and the boardroom
  keeps your question.** Telling a finished project "change of plans" used to be refused as
  self-growth, the notice pointed at a desk that held nothing under CEO plan authority, and the
  replacing chat session filed your question as history. A confirmed brief now lifts the freeze;
  proposals the CEO cannot accept land on your desk; and a chairman line with no answer after it is
  owed, read or not. ([#99](https://github.com/dominicplouffe/yaaf/pull/99))
- **Recalling an agent clears the stand-down condition it overrules.** A recalled writer was stood
  down again three seconds later by the condition that had already fired — twice in one day, while
  the CEO was telling the chairman she was writing. A condition the CEO has already moved to the new
  job's end is kept. ([#100](https://github.com/dominicplouffe/yaaf/pull/100))
- **The Recover check is graded on any report written under `out/`.** The refusal tells the model to
  write under `out/` without naming a file, but the grader accepted only `out/report.md` — a path no
  prompt mentioned — so every model failed a check it had done correctly. ([#101](https://github.com/dominicplouffe/yaaf/pull/101))

### Removed

- **The TypeScript build cache is no longer in the repository.** A source checkout stopped showing a
  modified file after every build; the cache is recreated by the next build. ([#98](https://github.com/dominicplouffe/yaaf/pull/98))

## [0.4.0] - 2026-10-28

**No action required for an existing install.** Every seat keeps the model, harness and price it was
already running on — the provider change ships with a migration that pins them — and an install that
never chose a provider behaves exactly as it did before. Two things become possible rather than
required: connect more providers, and name a web-search backend. The update script applies the
database changes.

### Added

- **Every part of yaaf runs on any model provider.** A seat can run on the Claude Agent SDK, the
  Claude CLI, yaaf's own loop against Anthropic's or any OpenAI-compatible API (DeepSeek, OpenRouter,
  Kimi, Qwen, Mistral, Grok, a server of your own), the Codex CLI, the Gemini CLI or OpenCode, and
  every one of them is metered against the project's own cap. Roles name a tier that each provider's
  lineup resolves, Settings → Providers holds the keys and shows what each provider can run, and a
  provider check tells you whether a model really answers before a seat is put on it. A provider
  whose API cannot search needs a search backend — Brave or Tavily, with your key, priced per query
  into the run — before a role that uses the web can run there. A credential that keeps failing
  fails over to its backup, and the install's daily upkeep pass can keep prices and model lists
  current. ([#67](https://github.com/dominicplouffe/yaaf/pull/67))
- **Pages you open every day are one click away, and a new project has a setup checklist.** Any
  Details tab, Activity view, Settings section or single file can be pinned under the project's
  pages (Deliverables comes pinned; up to five per person, saved on your account, reachable with G
  then 1–5). A new project's Inbox lists the six things it needs — model key, brief, data sources,
  secrets, integrations, people — each ticked off by the project's own state, and the key step
  cannot be skipped. Settings moved to its own address, and old links redirect.
  ([#72](https://github.com/dominicplouffe/yaaf/pull/72))
- **One seat can change its model, effort or harness after the project exists.** Previously that
  meant a new role version for the whole team, or a new team. The change applies from that agent's
  next run, and a seat that cannot run is refused and left as it was. ([#73](https://github.com/dominicplouffe/yaaf/pull/73))
- **The Founder interviews in rounds until nothing is assumed, with its recommendation preselected.**
  Each question arrives with an answer already picked and what the answer moves, every question whose
  prerequisites are settled, and agreeing with a whole round is one button — so the draft is still a
  draft, not an interrogation. ([971d062](https://github.com/dominicplouffe/yaaf/commit/971d06270))

### Changed

- **The install can pay for what a project does not, and its defaults back every seat.** The install
  gains its own model and harness settings, and the credential it already uses for the Founder also
  pays any seat whose provider no project credential pays. A seat resolves its own override, then
  its role, then the install's defaults — which ship as what an install already had.
  ([#78](https://github.com/dominicplouffe/yaaf/pull/78))
- **One provider: roles carry the effort, the install carries the model and harness.** The shipped
  role library no longer names a model, so the install's default decides. Settings → Models names the
  model and harness; the Roles page drops its model control; the project's Money card names the
  provider that pays. The platform's own key pays a keyless project's runs and never its billing, and
  a borrowed key never pays a project's seats. ([#79](https://github.com/dominicplouffe/yaaf/pull/79))
- **The seat editor names a provider the project does not pay the moment you pick a model on it, and
  puts the door where Save was.** Previously the refusal arrived at Save, with no way forward from
  that screen. ([#76](https://github.com/dominicplouffe/yaaf/pull/76))
- **The seat picker lists every provider, marks the ones the project does not pay, and pays one in a
  single press.** Choosing a model there opens the door in place: pick a credential the install
  holds, press Connect & save, and the provider is paid and the seat moved together. The server's
  checks are unchanged — only the round trip is gone. ([#77](https://github.com/dominicplouffe/yaaf/pull/77))
- **That door pays the project's own provider again**, with the install's provider as the default a
  project falls back on, reachable through a quiet secondary link rather than demanded of every seat.
  ([#80](https://github.com/dominicplouffe/yaaf/pull/80))
- **A project is a team at work: the wall of tabs becomes a few pages, each answering one question**,
  with the detail inside the page rather than beside it. ([#61](https://github.com/dominicplouffe/yaaf/pull/61))
- **A project's home is its Inbox, and the Team page holds up with large teams.**
  ([#63](https://github.com/dominicplouffe/yaaf/pull/63))
- **Data and credentials are two pages a person can read.** Data sources and secrets are one line per
  thing under Connections, with edits in a drawer, an access map showing what can read what, plain
  values, and data-source health in words. Rotating a key no longer drops the grants that key had.
  ([#64](https://github.com/dominicplouffe/yaaf/pull/64))
- **Setup & health advice is plain, actionable, and asked once.** A suggestion names the thing to
  change, and putting it away keeps it away. ([#60](https://github.com/dominicplouffe/yaaf/pull/60))
- **A brief draft shows what it changes, not the whole brief again.** ([#59](https://github.com/dominicplouffe/yaaf/pull/59))
- **An Operate project no longer carries a delivery date**, because its work repeats rather than
  finishes. ([#55](https://github.com/dominicplouffe/yaaf/pull/55))
- **Done, Approve and Noted act on the first press, with the note optional beside them.** A note on
  a Done used to be a second click to skip a text box; saying no still asks why, because that is what
  the CEO re-plans from. ([fd848cc](https://github.com/dominicplouffe/yaaf/commit/fd848ccaf))
- **Each decision is one button that names its outcome** — Done, Raise to 60, Approve $199, Noted —
  with an arrow beside it for the yes that needs words or the no that needs a reason, opening a
  labelled box in place of the row. ([b869d56](https://github.com/dominicplouffe/yaaf/commit/b869d5683))
- **The assist's "no" is called Not done, not Couldn't**, because mostly you chose not to rather than
  tried and failed. ([978f84c](https://github.com/dominicplouffe/yaaf/commit/978f84cf5))
- **The README says what the website says: build a company, hire a CEO.**
  ([#74](https://github.com/dominicplouffe/yaaf/pull/74))
- **The docs say what the app now does** — one provider chosen for the install, roles carrying the
  effort — and the class-era sentences are gone from the project and integration guides.
  ([729a721](https://github.com/dominicplouffe/yaaf/commit/729a7218c))

### Fixed

- **Every click on the inbox now means something.** Approve and Deny sat on requests where the click
  changed nothing — sixteen capacity approvals were granted and none moved a dial, and one desk filed
  the same raise three times. Approving a capacity request now raises that dial through the amendment
  path; every button names its effect; and a heads-up must quote the standing order that asked for
  it. ([fa464d4](https://github.com/dominicplouffe/yaaf/commit/fa464d45d))
- **What you write in one place is now read in every place.** A note you left on an Inbox decision
  was answered by a work run in a box you never open, then a fresh chat session told you it was still
  waiting on you, and a note was answered twice. The CEO's boardroom lines and its work runs are
  separate processes with no shared memory, so the boardroom is now the record they all read: a note
  on any decision is your line there, the chat lane no longer consumes notices the planning lane
  needs, every lane sees what the CEO last said, and a fresh session no longer reads platform lines as
  its own words. ([8964567](https://github.com/dominicplouffe/yaaf/commit/896456740))
- **Every route your words arrive by is one path.** Your words on twenty-five other decisions — a cut
  or reworded requirement, an audit, a review, a commitment, an overrun, a paused project, a
  stood-down team, an extended hypothesis — used to reach the CEO only as a notice that expired after
  a day. Every one now goes through the same record, and the test suite fails on any route that does
  not. ([b867fb7](https://github.com/dominicplouffe/yaaf/commit/b867fb714))
- **The boardroom chat sees what is still waiting, not a day of work already done.** It was shown the
  last 24 hours of notices on every turn — ten thousand characters, nine of them "say it in the
  boardroom now" for flags its own runs had already raised. ([40086cd](https://github.com/dominicplouffe/yaaf/commit/40086cdd8))
- **A note on a decision now gets the answer written to it, and never waits for one.** The answer
  used to be taken from the room — the CEO's first line after yours, whatever it was about — and a
  note on a decision bought no wake at all when the chat session was off, so it could sit until
  something else woke the CEO. ([#75](https://github.com/dominicplouffe/yaaf/pull/75))
- **The boardroom stops warning about a refusal the CEO already got past.**
  ([#58](https://github.com/dominicplouffe/yaaf/pull/58))
- **Moving between pages no longer lands you at the bottom of the page.**
  ([#56](https://github.com/dominicplouffe/yaaf/pull/56))
- **Every link that threw "s is not a function" after the scroll reset works again.**
  ([#57](https://github.com/dominicplouffe/yaaf/pull/57))
- **Every text colour meets 4.5:1 on the ground it actually sits on.** A contrast audit of every
  screen in both themes found about 950 failing text nodes: the muted grey failed on the sidebar and
  on tab strips, avatar initials were painted in chart-slot colours (down to 2.24:1), and finished,
  paused and gone rows faded with opacity until the text inside them failed. It also found text set
  at 11px. The worst case now clears 4.94:1. ([#68](https://github.com/dominicplouffe/yaaf/pull/68))
- **The same colours clear 4.5:1 on hovered grounds too** — a hovered sidebar row, the command
  palette's selected shortcut, and muted text on a hovered card. The worst case is now 4.73:1.
  ([#69](https://github.com/dominicplouffe/yaaf/pull/69))
- **Two focus states that drew nothing now show a ring** — the "in your own words" box in the
  founding interview, and items in a split menu. ([#70](https://github.com/dominicplouffe/yaaf/pull/70))
- **The disabled Send button is no longer blank, and nothing is set smaller than 12px.** Twenty-four
  rules were below the floor the type scale promises, including the team's "lead" tag and the
  roadmap's dependency labels. ([#71](https://github.com/dominicplouffe/yaaf/pull/71))
- **The undo toast is readable.** Its text colour pointed at a token that does not exist, so it was
  white on near-white in the dark theme. ([#65](https://github.com/dominicplouffe/yaaf/pull/65))
- **Nothing in the stylesheet reads a colour token that nothing declares.** The same mistake drew
  every warning in NewProject and Settings in grey; the stylesheet test now refuses it, fallback or
  not. ([#66](https://github.com/dominicplouffe/yaaf/pull/66))

## [0.3.0] - 2026-10-21

**No action required.** The unread count starts at zero rather than counting your history: existing
members are stamped as having read the boardroom up to the moment of the upgrade. The update script
applies the database changes.

### Added

- **An unread count on the boardroom.** Each member gets a read mark, and the badge counts boardroom
  messages after it written by anyone else, on the project card and on each desk row. The mark moves
  only when you look at the tab or when you speak, and never backwards. Slack offers no read
  receipts, so reading there alone does not clear it. ([85c272b](https://github.com/dominicplouffe/yaaf/commit/85c272b1d))
- **A confirmed brief holds a change of direction.** When you change direction the CEO now writes it
  back to you as a brief: the goal in plain words as it now stands, every standing order with its
  number, and what no longer applies, quoting what you said. Confirm it and it replaces the founding
  goal and puts the orders in every agent's prompt on every run; retired orders are listed too, so an
  agent that finds the old rule in a file or a memory knows it is dead, and the CEO is told to
  rewrite the files, scripts and memories that still say the old thing. It lands on Decisions,
  editable, and Settings → Brief edits the same three parts. This also fixes a shared read mark that
  let the chat session eat your messages every two seconds, so work runs never saw them.
  ([e26279f](https://github.com/dominicplouffe/yaaf/commit/e26279f38))

### Changed

- **The inbox is quiet when there is nothing in it, and the health card names the seats that never
  run.** An inbox reading zero is a line rather than a banner; a seat with no run in a week is named
  on the health card and the Team page; and the header badge says "problem" or "suggestion", goes to
  Setup & health, and can be put away per person. ([#52](https://github.com/dominicplouffe/yaaf/pull/52))
- **The CEO writes for a reader with no context.** Everything a person reads — boardroom messages,
  questions, approvals, flags, reviews — must now stand on its own: things named by what they are
  rather than by an ID or a platform word, every number given with what it is measured against,
  times in your own timezone, and a decision put as what is going on, why it matters, the options,
  the recommendation, and what happens if you say nothing. ([#54](https://github.com/dominicplouffe/yaaf/pull/54))
- **A deliverable announced in the boardroom opens the file itself.** The name links straight to that
  file's preview instead of telling you it is on the Deliverables page.
  ([8219224](https://github.com/dominicplouffe/yaaf/commit/821922412))

### Fixed

- **Waits that end by themselves raise no banner.** A scheduled start, pacing, the wake ceiling and
  batching are quiet now, with a "resumes in …" line on the Overview instead of a health finding.
  ([#53](https://github.com/dominicplouffe/yaaf/pull/53))
- **A published deliverable is announced in the boardroom, not only in Slack.** Publishing wrote to
  the Slack channel and never to yaaf's own boardroom, so Slack knew something the boardroom did not.
  ([a2abfbe](https://github.com/dominicplouffe/yaaf/commit/a2abfbe8a))

## [0.2.0] - 2026-10-14

**You need to do three things when you upgrade.**

1. **Sign in with an account — the static admin token is retired.** `YAAF_ADMIN_TOKEN` is no longer
   read by anything. Set `YAAF_ADMIN_EMAIL` (or pass `--admin-email`) and the deploy or update script
   makes that person the instance admin and prints a one-time link to set their password; invite
   everyone else from Settings. Scripts that sent the old token get refused: log in with `yaaf login`
   or hand them a personal token in `YAAF_TOKEN`.
2. **Call the single-server scripts from `deploy/`.** They moved beside the compose file they drive:
   `./update_single_server.sh` is now `deploy/update_single_server.sh`, and the same for the deploy
   and kill scripts. Any cron entry or note that names the old path needs changing.
3. **Set `YAAF_PUBLIC_BASE_URL` if you serve yaaf over https.** It is now in `deploy/.env.example`
   instead of only in the deployment doc. It is what makes session cookies `Secure` and what MCP
   discovery advertises; changing it later changes the MCP audience, so connectors you have already
   added have to be connected again.

The update script applies the database changes for you.

### Changed

- **People sign in.** The dashboard and the CLI now authenticate a person rather than a client:
  accounts with passwords, browser sessions, personal tokens for scripts, and per-project roles with
  co-chairs who can decide. MCP connectors are unchanged — MCP tokens work as they did.
  ([#47](https://github.com/dominicplouffe/yaaf/pull/47))
- **The single-server scripts live in `deploy/`**, next to `_common.sh` and the compose file they
  drive, and every doc and on-screen hint names the new path.
  ([49c6e4d](https://github.com/dominicplouffe/yaaf/commit/49c6e4ddb))
- **`/app` keeps the browser's scheme behind a TLS proxy.** Behind a proxy that terminates TLS, `/app`
  used to redirect to `http://host/app/` — the plain-http hop a browser marks "Not Secure" — because
  the redirect was built from the scheme the server sees. It now answers with a relative path, and
  `deploy/.env.example` lists `YAAF_PUBLIC_BASE_URL`, which an install that left it unset got
  non-`Secure` cookies and MCP discovery naming `http://api:8080` from.
  ([c9a2754](https://github.com/dominicplouffe/yaaf/commit/c9a2754ba))
- **Any harness, any provider, the same rules.** A seat can run on the Claude Agent SDK, the Claude
  CLI, yaaf's own loop against the Messages API or any OpenAI-compatible server, the Codex CLI, the
  Gemini CLI or OpenCode — and every one of them is metered against the project's one cap. The
  boardroom runs on the CEO's own seat. ([#45](https://github.com/dominicplouffe/yaaf/pull/45))
- **The Deliverables list is served by the server.** The tab used to walk the whole workspace and ship
  every file to the browser every 15 seconds per tab; on a large project the polls piled up and the
  tab stopped loading. A per-project index answers search, filters, sort and day folders, the screen
  fetches only the rows it shows, a stale reply can no longer overwrite a newer one, and responses are
  compressed (file downloads excepted). ([#46](https://github.com/dominicplouffe/yaaf/pull/46))
- **The project pages are rebuilt around three questions**: what needs you, who is doing what, and is
  it on track — one Inbox, a live org chart, and an Overview you can read in five seconds.
  ([#49](https://github.com/dominicplouffe/yaaf/pull/49))
- **A deliverable can be 100 MB, up from 5 MB.** The old ceiling refused a ten-minute podcast mp3 at
  9.5 MB. This is a trial: an upload is still one base64 request body and downloads are still
  buffered, so a large file takes longer to publish and holds memory while it does.
  ([435a210](https://github.com/dominicplouffe/yaaf/commit/435a21023))
- **Swapping a model key reaches the boardroom.** The CEO's chat session lived for days on the
  credential it started with, so replacing an exhausted key with a funded one left the boardroom
  answering "session limit" while every work run paid with the new one. The session records which
  credential pays it and restarts when that changes. ([de196df](https://github.com/dominicplouffe/yaaf/commit/de196df9c))
- **The docs say what the product is.** The project's history starts on 14 August 2026, and the
  monthly budget is written as a plan that obliges an account when it is crossed, with the wall above
  it — not as the wall itself. ([#48](https://github.com/dominicplouffe/yaaf/pull/48))

### Added

- **An agent inbox, on screen, in the CLI and over MCP.** What the agents are asking you for has its
  own view, your own chat bubble is readable rather than low-contrast, and the inbox banner says in
  plain words what it is about. ([#51](https://github.com/dominicplouffe/yaaf/pull/51))

### Fixed

- **A model key that does not cover the team's models now says so, and offers the way out.** The
  founding flow used to accept a key for one provider over roles naming models on another and only
  fail at the end. ([#50](https://github.com/dominicplouffe/yaaf/pull/50))

## [0.1.1] - 2026-10-07

**No action required.** This release changes no database schema and adds no settings.

### Changed

- **Every seat moves to the 5.5 generation, and Opus costs about a fifth less.** All roles and
  defaults now name Opus 5.5 and Sonnet 5.5. Opus 5.5 lists at $4 and $20 per million tokens against
  Opus 5's $5 and $25; Sonnet 5.5 is priced like Sonnet 5. A high-stakes seat still on Opus 5 is
  lifted to 5.5 rather than left on the dearer model. ([43b315c](https://github.com/dominicplouffe/yaaf/commit/43b315c7e))

### Fixed

- **Signing in to an MCP connector survives a change of address.** Registration binds a client to the
  callback it named, and yaaf only registered when it had no client on file — so after the install's
  hostname changed (a new Tailscale name, for instance) every sign-in was refused with
  `invalid_redirect` and nothing on yaaf's side said why. It now records the address each client was
  registered for and registers again when that address has moved; a client you pasted in by hand is
  never replaced. ([1cd1747](https://github.com/dominicplouffe/yaaf/commit/1cd1747a1))
- **The settings rail lines up, and the selected item is the bold one.** Items were centred under
  left-aligned headings and every idle item was heavier than the selected one, because a global
  button rule overrode the rail's own. ([56a4d55](https://github.com/dominicplouffe/yaaf/commit/56a4d5594))
- **The CEO can put a team task on the board.** Every field of the task tool was declared required,
  and a team task must leave the parent, assignee and hypothesis out — so the call was refused when
  they were omitted and refused again when they were filled with empty values. One desk spent eight
  refusals on it over two runs and left 26 accepted requirements with no task on the board. Fields a
  tool may be called without are now declared optional, which also unblocks data-source discovery and
  clearing a stand-down condition. ([d6878e5](https://github.com/dominicplouffe/yaaf/commit/d6878e585))

## [0.1.0] - 2026-09-20

The version the repository was at when this changelog begins, tagged on the last commit before
1 October 2026 ([b588a33](https://github.com/dominicplouffe/yaaf/commit/b588a33)). Its release notes
are not in this file.

## Notes on method

**The week.** Seven days, Thursday to Wednesday, counted from Thursday 1 October 2026: 1–7 October,
8–14 October, and so on. Each release is dated by the last day of its week. The cut is made in UTC,
because the public timestamps of a merge — the pull request's merge time, the date a GitHub Release
carries — are UTC, so that anybody checking the count against GitHub gets the same week boundaries
this file did.

**What counts as one change.** One pull request merged into the default branch during the week is one
change. One direct commit to the default branch that did not come through a pull request is one
change, and its SHA is the link in its entry. These count as zero: merge commits themselves (the pull
request they merge is counted once, for the pull request), reverts of something merged in the same
week, and changelog or version-bump commits made by this project.

**The version.** Six or more changes: the minor version goes up by one and the patch returns to zero
(0.2.4 becomes 0.3.0). Five or fewer, including exactly five: the patch goes up by one (0.1.0 becomes
0.1.1). A week with no changes gets no release and the version does not move.

**Where the numbers come from.** `scripts/count_changes.sh` in the repository derives every week's
count from `git log --first-parent` alone — no token, no network, no API quota — classifies each
entry as a pull request or a direct commit from its own commit message, and prints the version chain
with the counted PR numbers and commit SHAs under each week. Anybody can re-run it against a clone
and get the same numbers. The count was also checked once against GitHub's own list of merged pull
requests: for each of the five weeks the set of PR numbers the script reports is identical to the set
GitHub records as merged into `main` in that week, and every entry the script reports as a direct
commit returns no associated pull request from GitHub.

**Two changes move a week depending on the time zone, and no version moves.** PR #52 was merged at
2026-10-15T01:02Z and PR #81 at 2026-10-29T00:04Z. Read in the chairman's Eastern time they land in
the previous week, which would make the weekly counts 4, 12, 6, 35 and 22 instead of 4, 11, 7, 34 and
23. Under either cut every week except the first is still six or more, so the version chain is
identical: 0.1.1, 0.2.0, 0.3.0, 0.4.0, 0.5.0. The 1–7 October week has the same four changes under
both.

**One entry per change.** Every counted change appears exactly once above, under exactly one kind, so
a week's count can be checked by counting the entries in its section. Where one change would fit two
kinds it is filed by what an operator notices most. Where a change was replaced later in the same
week — #81's token conversion replaced by #82, and the seat editor's three passes in #76, #77 and #80
— both entries are kept and each describes what that change did when it landed, so the arithmetic of
the week stays visible. The last of them states the behaviour you end up with.

**Database changes.** The pull request that carries this file also sets the version string in
`pyproject.toml` and `yaaf/__init__.py` to the newest tag it publishes, 0.5.0, so an installed
package reports the same version as the release it came from. Every release from 0.2.0 on adds
database migrations. `deploy/update_single_server.sh` applies them on a server and `yaaf up` applies
them on a desktop install; an install driven by hand needs `alembic upgrade head`. No migration in
these five weeks deletes rows when it is applied. Six of them rewrite rows that are already there,
and this is what each does to yours: 0046 lower-cases the stored user emails, because sign-in now
matches on them exactly, and gives every existing project's chairman the membership row creation
writes today (0.2.0); 0048 stamps existing members as having read the boardroom up to the upgrade,
so the new badge does not count your history (0.3.0); 0054 fills in `secrets.updated_at` from
`created_at` where it was empty (0.4.0); 0055 renames the stored harness names to `yaaf_loop` and
pins the CLI that an OpenAI or Google API key used to drive, so no seat changes engine on deploy
(0.4.0); 0063 moves a cut obligation back to the cycle it was cut in and re-says the old cycle
scorecards (0.5.0); and 0064 re-counts the "Met since last" figure frozen into each meeting
(0.5.0).

[0.5.0]: https://github.com/dominicplouffe/yaaf/releases/tag/v0.5.0
[0.4.0]: https://github.com/dominicplouffe/yaaf/releases/tag/v0.4.0
[0.3.0]: https://github.com/dominicplouffe/yaaf/releases/tag/v0.3.0
[0.2.0]: https://github.com/dominicplouffe/yaaf/releases/tag/v0.2.0
[0.1.1]: https://github.com/dominicplouffe/yaaf/releases/tag/v0.1.1
[0.1.0]: https://github.com/dominicplouffe/yaaf/releases/tag/v0.1.0
