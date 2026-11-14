# Changelog

Release notes for [yaaf](https://github.com/dominicplouffe/yaaf). One release per week, written in
the [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) form and numbered by the chairman's
rule.

A week is seven days counted from Thursday 1 October 2026: 1 to 7 October, 8 to 14 October, and so
on. Each release is dated by the last day of its week. The number a release gets is arithmetic, not
judgement: six or more changes bump the minor version and reset the patch, five or fewer bump the
patch, and a week with no changes gets no release and the number does not move.

Every counted change has exactly one entry below, and the pull request number or commit SHA the
count was made from is the link at the end of that entry. Count the entries in a section and you
have that week's change count, and therefore its version. [Notes on method](#notes-on-method) at the
end says how the counting is done and where the week boundary falls.

| Release | Week | Changes | In one line |
|---|---|---|---|
| **0.6.0** | 5-11 Nov 2026 | 21 | Each data source tool needs your permission, projects get mailboxes, and yaaf runs on your own desktop |
| **0.5.0** | 29 Oct – 4 Nov 2026 | 23 | One honest meter for what a run costs, and runs that stop being cut short |
| **0.4.0** | 22–28 Oct 2026 | 34 | Any model provider, one provider chosen for the install, and an inbox whose buttons do something |
| **0.3.0** | 15–21 Oct 2026 | 7 | A confirmed brief holds a change of direction, and the boardroom shows what you have not read |
| **0.2.0** | 8–14 Oct 2026 | 11 | People sign in, the admin token goes, any harness runs a seat, and the Deliverables tab stops falling over |
| **0.1.1** | 1–7 Oct 2026 | 4 | Cheaper Opus, a connector that survives an address change, and a CEO that can open a task |
| **0.1.0** | none | none | The starting point: the last commit before 1 October 2026 |

Releases are listed newest first.

## [0.6.0] - 2026-11-11

**Yes, three things need you.** First, if agents write through a data source, open **Settings → Data sources → Tools & permissions** and allow the tools they write with. A data source saved without a tool list is no longer unrestricted. Read tools keep their access. Write tools and tools yaaf cannot classify are refused until you allow them. Second, a script that runs `yaaf create-project SPEC` must now run `yaaf create-project --from-file SPEC`. Third, if you run the optional S3 artifact store, write `MINIO_ROOT_PASSWORD` and the matching `YAAF_ARTIFACT_S3_ENDPOINT`, `YAAF_ARTIFACT_S3_ACCESS_KEY` and `YAAF_ARTIFACT_S3_SECRET_KEY` into `deploy/.env` before you upgrade. Set those values first, then run `deploy/update_single_server.sh`. The update script never writes the password, and it starts the stack without the s3 profile. MinIO then does not start, and artifacts already stored in MinIO stop showing in the app. The default for `YAAF_API_HOST` also moved to 127.0.0.1. Set it if `yaaf-server` runs on the host and you reach the dashboard from another machine. The update applies the database changes itself.

**The count.** This release has 21 changes. The script scripts/count_changes.sh in the repository derives that count. The 21 counted pull requests and commits are linked below.

### Changed

- **A data source call now needs a permission for each tool, and only you can give it.** Every call goes through yaaf, and yaaf checks the tool's permission before it forwards the call. Read tools start allowed. Write tools and unclear tools start refused, and a refused call sends nothing to the source. The source's address and token no longer reach the agent sandbox, so an agent can no longer call a source directly. A source saved with a tool list keeps that list. A pending one-call grant closes, because a denied tool now fails every time. ([#120](https://github.com/dominicplouffe/yaaf/pull/120))
- **Work runs write their prompt cache for 5 minutes, not 1 hour.** A work run calls the model again within seconds, so the hour costs more than it saves. The resident chat keeps the hour. The same change holds a queued run until the plan's own reset time, instead of starting it into a refusal. ([50c2e41](https://github.com/dominicplouffe/yaaf/commit/50c2e41fc))
- **yaaf can email you when a CEO waits for your answer.** Two rules sit under **Rhythm & reporting**. **Email me when** chooses which notices may become mail, and the default is a CEO waiting on you. **Minutes to answer in the app** holds the request for 60 minutes and mails it only if it is still open. An answered or snoozed request is never mailed. ([699a8bb](https://github.com/dominicplouffe/yaaf/commit/699a8bb6d))
- **A Y in the top bar shows that yaaf runs and what waits.** `Y 13` counts decisions and unread messages from a CEO. yaaf highlights it when a decision will decide itself soon or a project hit its spending wall. It shows `Y ✕` when yaaf is down. A click opens the panel with every row linked to its screen. The tray icon carries the same answer, and a CEO message notifies you at most once every ten minutes. ([#115](https://github.com/dominicplouffe/yaaf/pull/115))
- **You can stop a Founder turn and correct what you sent.** The progress panel has **Stop and edit**. It cancels the turn, takes your last message back off the thread and puts it in the box, focused. Your corrected message replaces the first one. ([#114](https://github.com/dominicplouffe/yaaf/pull/114))
- **`yaaf create-project` now runs the Founder conversation from the terminal.** Give it a brief and the Founder drafts the project and asks its questions at the prompt. The round of questions appears during the turn, not after it. The menu writes the draft to a file, which `yaaf rehearse` tries first. ([#111](https://github.com/dominicplouffe/yaaf/pull/111))
- **The terminal now asks for the keys your agents need, and lists what is left to set up.** Each missing key is asked for with hidden input and stored for the hired roles that need it. Enter skips a key, and skipped keys are listed at the end with the command that sets them. Each unfinished setup step is printed with a link to the dashboard section that does it. ([#112](https://github.com/dominicplouffe/yaaf/pull/112))
- **The docs are rewritten, and the deployment gaps found while writing them are closed.** The docs folder keeps six pages: INSTALL, CONFIGURATION, DEPLOYMENT, ARCHITECTURE, PROVIDERS and CLI. The optional MinIO store has no default password now, and MinIO refuses to start without one. `deploy/deploy_single_server.sh` writes a random one. `deploy/update_single_server.sh` does not write one, so an upgrading box reads `MINIO_ROOT_PASSWORD` from `deploy/.env` first. MinIO publishes on 127.0.0.1 only. Its settings reach api and runner through `deploy/.env`, which is now read whole. `yaaf-server` listens on 127.0.0.1 by default, and the image sets 0.0.0.0 inside its own network. `yaaf backup`, `yaaf restore` and `yaaf rotate-master-key` are new. ([#109](https://github.com/dominicplouffe/yaaf/pull/109))
- **The change count no longer counts our own release commits.** The script now drops a mainline commit when it changes `CHANGELOG.md` and no file outside `CHANGELOG.md`, `pyproject.toml` and `yaaf/__init__.py`. A release pull request that also changes code still counts. The counts of the five earlier weeks do not move. ([#123](https://github.com/dominicplouffe/yaaf/pull/123))

### Added

- **The repository now carries the script that derives every version.** `scripts/count_changes.sh` prints each week's dates, its change count and the version that week gets. It reads the clone and nothing else, so a plain clone gives the same numbers with no token. ([#119](https://github.com/dominicplouffe/yaaf/pull/119))
- **One page shows what every provider, model and project cost.** **Settings → Spend** charts the install by day, week or month, in dollars or tokens, and its filters travel in the URL. The same answer comes from `yaaf spend` on any machine and from the MCP tool `spend_across`. ([#106](https://github.com/dominicplouffe/yaaf/pull/106))
- **A project can have its own mailbox.** Set the address, the SMTP and IMAP servers, the password and a list of people in **Settings → Email**. The CEO can email a finished deliverable and read the reply on the same address. Only the CEO sends or reads, and only to a listed address. The password never reaches an agent. ([#116](https://github.com/dominicplouffe/yaaf/pull/116))
- **A project whose work runs through email cannot start without its mailbox.** A brief that describes email sets the need. So does a checkbox on the new-project form, or a switch in **Settings → Email**. A listed sender may write first, up to 20 conversations a day. An inbound attachment is kept up to 10 MB, and the workspace gets a copy. ([#118](https://github.com/dominicplouffe/yaaf/pull/118))
- **yaaf now runs on your own desktop without Docker.** `yaaf up --host` runs each agent in a bubblewrap jail, and `deploy/install_desktop.sh` installs the rest. A new install listens on 127.0.0.1:24247 and serves the dashboard there. `yaaf signal` and `yaaf desk` put the waiting decisions in a status bar and a terminal. The Omarchy layer adds a panel, a menu and a shortcut. ([#108](https://github.com/dominicplouffe/yaaf/pull/108))
- **`yaaf describe` did not ship, and the command is `yaaf create-project`.** #110 brought the dashboard's Founder conversation to the terminal under that name. #111 renamed it before the release shipped, so the command an operator types is `yaaf create-project`. ([#110](https://github.com/dominicplouffe/yaaf/pull/110))
- **Each Founder turn now records where its time went.** The record covers CLI start-up, every model call with its tokens and thinking, and every tool. The server log gets one line per turn. `yaaf create-project --timing` prints the line, and the thread API returns it. ([#113](https://github.com/dominicplouffe/yaaf/pull/113))

### Fixed

- **The runner no longer reaps runs that wait for a worker.** It takes a run only when a free worker can start it. A run that waits for a worker keeps its place, and the runner no longer kills it first. ([#107](https://github.com/dominicplouffe/yaaf/pull/107))
- **A deliverable with an em dash in its name now downloads.** The download answered 500 because the header could not carry the character. The real name now travels as UTF-8, with an ASCII stand-in for older clients. ([#105](https://github.com/dominicplouffe/yaaf/pull/105))
- **The row menu is no longer cut off by its list.** It showed only its first item on the last row of a list, so Edit and Remove… were out of reach. It now shows every item, and it opens upward near the bottom of the window. Every row menu in the app gets the fix. ([#117](https://github.com/dominicplouffe/yaaf/pull/117))
- **The Deliverables page opens on Published, and menu items line up on the left.** Published is what was delivered, and All and Working are one press away. A link to a working file still finds that file. ([#104](https://github.com/dominicplouffe/yaaf/pull/104))
- **The sandbox image builds again with a newer playwright.** The image lacked a library that the new playwright's node needs, so the build stopped. The library now has its own layer, and the layers above it do not rebuild. ([fc13818](https://github.com/dominicplouffe/yaaf/commit/fc138184d))

## [0.5.0] - 2026-11-04

**One thing you can decide. Nothing you must do.**
A project keeps the run limits that it had when you created it.
On the default cards, that is a $2 cap on one run and a 30-minute run timeout.
The new $20 and 90 minutes apply to projects that you create from now on.
If your runs still stop before the work is done, change **Most one run may spend** and **Run timeout
(seconds)** in that project's Settings.
Nothing else needs you.
The update script applies the database changes.
You do not need a new credential.

**The count.** This release has 23 changes. The script `scripts/count_changes.sh` in the repository derives that count.

### Changed

- **Every provider now meters against the same budget.**
  A project keeps its monthly cap in tokens for every provider.
  A DeepSeek key and a Claude subscription that pay seats on one project now count against one
  ceiling.
  The screen shows the cost in dollars.
  A project that meters in dollars keeps its hard stop in dollars.
  The next entry replaced the conversion of dollar budgets into token caps before the release
  shipped. ([#81](https://github.com/dominicplouffe/yaaf/pull/81))
- **Mixed payers get an honest bill, and yaaf does not convert the two units.**
  The credential that paid a run now decides if dollars move.
  An API key books its real bill.
  A subscription books no dollars.
  On a dollar budget, yaaf counts a subscription seat at its own catalog price, and not at a guessed
  exchange rate.
  This replaces the conversion in #81, which was a guess.
  Cache reads change the tokens-per-dollar ratio approximately tenfold.
  Each project keeps the unit that it already used, and you can mix the two units in either
  direction. ([#82](https://github.com/dominicplouffe/yaaf/pull/82))
- **yaaf refuses a CEO message that you would need to read twice.**
  yaaf refuses a boardroom post over 150 words, or with a sentence over 30 words.
  The refusal says what to correct.
  The chat lane rewrites the message one time and sends the rewrite, so you always get an answer.
  This follows months of long answers to the rule "plain English, three sentences".
  One message had 206 words in two sentences.
  ([#87](https://github.com/dominicplouffe/yaaf/pull/87))
- **The review of the CEO obeys the same length limits as the boardroom.**
  ([#88](https://github.com/dominicplouffe/yaaf/pull/88))
- **The review is a status meeting. It starts with what was done, then holds the plan of the CEO to
  account.**
  The meeting opens on what actually closed, from the record.
  The CEO files a status report that gives what it bought, what went wrong, what is next, and the
  ask.
  Each item in the report must serve something open, and yaaf can refuse any section of the report.
  The next meeting opens on that list, marked item by item.
  yaaf refuses a claim of "done" against an open requirement.
  The levers sit under the ask, with what each one moved.
  You can reply, and the CEO answers you.
  On an Operate rhythm, the meeting happens at the roll.
  ([#89](https://github.com/dominicplouffe/yaaf/pull/89))
- **One number now tells you what needs you, and every screen says where the work stands.**
  Home is a portfolio.
  It shows the decisions that wait on you, each with its verb.
  Then it shows one comparable row for each project.
  The row gives state, one sentence, outcomes, the next date, and money against the plan and the wall.
  A project opens on its decisions.
  After that come outcomes against the promise, what changed, what is next, money, and results.
  A finished delivery appears in "what needs you" as a sign-off.
  Each approval now carries the recommendation of the CEO in one sentence.
  yaaf stores that sentence, and does not guess it from the case.
  ([#90](https://github.com/dominicplouffe/yaaf/pull/90))
- **yaaf now ships the DeepSeek V4 models, and it marks a retired model that still answers as
  retiring.**
  The health checklist used to tell you that a seat on a retired DeepSeek model "would be refused by
  the provider".
  At the same time, a check on the same key had the model answer every call.
  DeepSeek retired `deepseek-chat` and `deepseek-reasoner` on 24 July and moved to the V4 lineup.
  A name that yaaf removed from the list and that still answers is a retired alias that DeepSeek has
  not switched off yet.
  On the day that DeepSeek switches it off, every seat on it stops at once.
  The warning stays, and its statement is now true.
  ([#92](https://github.com/dominicplouffe/yaaf/pull/92))
- **The cap on one run is now $20, and not $2.**
  At $2, the cap stopped 1,224 runs across 35 projects that did useful work.
  That includes 31 of the 59 dead runs of one project.
  In the previous 45 days, an ordinary run cost cents.
  99% of healthy runs finished below approximately $7, and the largest cost approximately $13.
  The Full send card is now $50.
  A project that you already have keeps its own value.
  Change it in the Settings of that project.
  ([#96](https://github.com/dominicplouffe/yaaf/pull/96))
- **The run timeout is now 90 minutes, and not 30.**
  The 30-minute limit stopped 338 runs across 12 projects, and 90 of them stopped in one week.
  Of the runs that timed out and were retried, 142 then finished, usually in seven more minutes.
  Healthy runs have a median below one minute and a 99th percentile of 23 minutes.
  Full send goes from 60 minutes to 3 hours.
  The $20 cap on one run is what stops a run that loops.
  ([#97](https://github.com/dominicplouffe/yaaf/pull/97))
- **Model lists and prices stay current, and yaaf meters long prompts correctly.**
  Upkeep is on by default.
  When yaaf reads the key of a provider, it sets the price and the description for the model list of
  that provider.
  The picker shows the price, the context window, and if a model reasons.
  It hides the models that do not chat.
  Prices now include their long-prompt tiers.
  A call above the line of a tier now pays the higher rate, and not half of what it spent.
  That problem billed Grok, Gemini Pro and GPT-6 at their base rate.
  yaaf fills an empty model class on a provider that sells its own models from the key list, by price
  group, newest first.
  A filled class changes only when you press.
  ([#102](https://github.com/dominicplouffe/yaaf/pull/102))
- **Settings is rebuilt. It opens on what needs you, and a provider is green only after a key
  answers.**
  The Overview sorts what needs you by what it stops, and gives the correction beside each item.
  It shows what each class runs on for each provider, and when that key last answered.
  A provider reads Ready only when a key has answered since the last change to its value.
  An answer can be a test, the model list, a graded check, or a run that the key paid.
  Without an answer, the provider reads Untested.
  It reads Failing when the provider refused the key.
  `yaaf doctor` names a refused key.
  ([#103](https://github.com/dominicplouffe/yaaf/pull/103))

### Fixed

- **A seat move now shows every problem before Save, and it can set up web search in place.**
  The seat check used to stop at its first problem.
  Moving a seat to DeepSeek gave a credential error, then a price error, then a web search error, one
  refusal for each click.
  The check now reports all problems at once, with the kind of each one.
  It also offers the way out: a web-search backend and its key, tested with one real query.
  After that, the seat saves.
  ([#83](https://github.com/dominicplouffe/yaaf/pull/83))
- **A sandbox now imports its own files on its own Python, or the build stops.**
  Python 3.12 accepted one string that was split across an f-string field.
  The Python 3.11 in the sandbox image did not accept it.
  Because of that, every seat on yaaf's own loop died at import, and yaaf reported its container
  as stopped.
  The image build and a CI job now import every shipped file on the base image interpreter.
  ([#84](https://github.com/dominicplouffe/yaaf/pull/84))
- **A deliverable in the chat now opens the file, and the project card opens a switcher.**
  The "Deliverable published" link used to send you to the Roadmap, because the redirect read the
  path of the parent route.
  The same change corrects the old `/tasks`, `/spend` and `/reviews` links.
  A deliverable now opens in the reader above the conversation.
  The project card in the sidebar now opens a project switcher, and not the command palette with
  "project" typed in.
  ([#85](https://github.com/dominicplouffe/yaaf/pull/85))
- **The boardroom now answers the question that you just asked.**
  A CEO on a summarising provider kept answering the first question of the session, three times in
  one day.
  The cause was the compaction.
  yaaf compacted each long conversation onto the first user turn of the transcript, and the newest
  message survived only in the summary.
  yaaf now writes the summary on the message that it answers.
  It never summarises a conversation between turns.
  Past half of the model window, it starts again from the boardroom, which is its record.
  ([#86](https://github.com/dominicplouffe/yaaf/pull/86))
- **A Founder draft whose team has no employees no longer breaks the review form.**
  The form called `.join()` on a list that was absent, and stopped before the end, so you could not
  pick any roles.
  A draft that you already saved in that shape now loads too.
  ([#91](https://github.com/dominicplouffe/yaaf/pull/91))
- **yaaf now judges a report that you owe every morning on each morning, and the scorecard says what it
  counts.**
  One weekly review said "0 of 4 met, 1 carried forward" for a report that arrived before its 08:00
  deadline every day.
  Three of the four were cut versions of the same report that were still in the denominator.
  yaaf judged the fourth only when all its work closed, and a daily pipeline never does that.
  A cut requirement no longer rolls forward.
  A daily check now runs daily.
  The sentence names the item that it counted.
  ([#93](https://github.com/dominicplouffe/yaaf/pull/93))
- **The review page no longer says three things that are not true.**
  A meeting that opened before the record existed no longer shows "No cycle closed this period" above
  seven cycles that closed.
  "Met since last" now counts requirements, and not events.
  An Operate project no longer shows one met next to "0 met".
  yaaf re-counts frozen meetings during the upgrade.
  A week that the platform never checked now reads "closed without being checked", and not "closed
  missed".
  ([#94](https://github.com/dominicplouffe/yaaf/pull/94))
- **A cut obligation no longer raises a "missed" flag.**
  A cut is a decision, and not a miss.
  The weekly roll wrote a cycle result for cut standing requirements.
  Each of those results raised "owed every cycle and cycle N closed without it".
  That gave 21 flags in seven weeks for three cut versions of one report.
  ([#95](https://github.com/dominicplouffe/yaaf/pull/95))
- **A brief that you confirm after the last requirement came true now reopens the project, and the
  boardroom keeps your question.**
  yaaf used to refuse "change of plans" to a finished project as self-growth.
  The notice pointed at a desk that held nothing under the plan authority of the CEO.
  The chat session that replaced it filed your question as history.
  A confirmed brief now lifts the freeze.
  A proposal that the CEO cannot accept lands on your desk.
  yaaf owes you an answer to a chairman line that has no answer after it, read or not.
  ([#99](https://github.com/dominicplouffe/yaaf/pull/99))
- **A recall of an agent now clears the stand-down condition that it overrules.**
  A recalled writer was stood down again three seconds later by a condition that had already fired.
  That happened twice in one day.
  At the same moment, the CEO told the chairman that the writer was at work.
  yaaf now keeps a condition that the CEO has already moved to the end of the new job.
  ([#100](https://github.com/dominicplouffe/yaaf/pull/100))
- **The Recover check now grades any report that is written under `out/`.**
  The refusal tells the model to write under `out/`, but it does not name a file.
  The grader accepted only `out/report.md`, a path that no prompt named.
  Because of that, every model failed a check that it had done correctly.
  ([#101](https://github.com/dominicplouffe/yaaf/pull/101))

### Removed

- **The TypeScript build cache is no longer in the repository.**
  A source checkout showed a changed file after every build.
  The next build creates the cache again.
  ([#98](https://github.com/dominicplouffe/yaaf/pull/98))

## [0.4.0] - 2026-10-28

**You must do nothing on an existing install.**
Every seat keeps the model, the harness and the price that it already used.
The provider change comes with a migration that keeps those values.
An install that never chose a provider behaves as it did before.
Two things become possible, and you do not have to do them: connect more providers, and name a
web-search backend.
The update script applies the database changes.

**The count.** This release has 34 changes. The script `scripts/count_changes.sh` in the repository derives that count.

### Added

- **Every part of yaaf can now run on any model provider.**
  A seat can run on the Claude Agent SDK or the Claude CLI.
  It can also run on yaaf's own loop against the Anthropic API or any OpenAI-compatible API.
  The compatible APIs include DeepSeek, OpenRouter, Kimi, Qwen, Mistral, Grok and a server of your
  own.
  A seat can also run on the Codex CLI, the Gemini CLI or OpenCode.
  yaaf meters every one of them against the project's own cap.
  A role names a tier, and the lineup of each provider has that tier.
  Settings → Providers holds the keys and shows what each provider can run.
  A provider check tells you if a model really answers before you put a seat on it.
  A provider whose API cannot search needs a search backend before a role that uses the web can run
  there.
  The backend can be Brave or Tavily, with your key, and yaaf prices each query into the run.
  A credential that keeps failing moves to its backup.
  The daily upkeep pass of the install can keep prices and model lists current.
  ([#67](https://github.com/dominicplouffe/yaaf/pull/67))
- **A page that you open every day is now one click away, and a new project has a setup checklist.**
  You can pin any Details tab, Activity view, Settings section or single file under the pages of the
  project.
  Deliverables comes pinned.
  You can pin up to five items for each person.
  yaaf saves them on your account, and you can open them with G and then 1 to 5.
  The Inbox of a new project lists the six things that it needs: model key, brief, data sources,
  secrets, integrations and people.
  The state of the project ticks off each one.
  You cannot skip the key step.
  Settings moved to its own address, and the old links redirect.
  ([#72](https://github.com/dominicplouffe/yaaf/pull/72))
- **One seat can now change its model, effort or harness after the project exists.**
  Before this change, that work needed a new role version for the whole team, or a new team.
  The change applies from the next run of that agent.
  yaaf refuses a seat that cannot run, and leaves it as it was.
  ([#73](https://github.com/dominicplouffe/yaaf/pull/73))
- **The Founder now interviews in rounds until it assumes nothing, and each question comes with its
  recommendation already selected.**
  Each question arrives with an answer already selected, and with what that answer changes.
  It shows every question whose prerequisites are settled.
  One button agrees with a whole round, so the draft stays a draft and does not feel like an
  interrogation.
  ([971d062](https://github.com/dominicplouffe/yaaf/commit/971d06270))

### Changed

- **The install can now pay where a project does not, and its defaults serve every seat.**
  The install gets its own model and harness settings.
  The credential that the install already uses for the Founder also pays any seat whose provider no
  project credential pays.
  A seat uses its own override first, then its role, then the defaults of the install.
  The install defaults are what an install already had.
  ([#78](https://github.com/dominicplouffe/yaaf/pull/78))
- **One provider now serves the install: roles carry the effort, and the install carries the model
  and harness.**
  The shipped role library no longer names a model, so the default of the install decides.
  Settings → Models names the model and the harness.
  The Roles page no longer has a model control.
  The Money card of the project names the provider that pays.
  The key of the platform pays the runs of a keyless project, and never its billing.
  A borrowed key never pays the seats of a project.
  ([#79](https://github.com/dominicplouffe/yaaf/pull/79))
- **The seat editor now names a provider that the project does not pay as soon as you pick a model on
  it. It also puts the way out where Save was.**
  Before this change, the refusal came at Save, and that screen gave you no way forward.
  ([#76](https://github.com/dominicplouffe/yaaf/pull/76))
- **The seat picker now lists every provider, marks the ones that the project does not pay, and pays
  one with a single press.**
  If you choose a model there, the way out opens in place.
  Pick a credential that the install holds, and press Connect & save.
  yaaf pays the provider and moves the seat together.
  The server checks are the same.
  Only the round trip is gone.
  ([#77](https://github.com/dominicplouffe/yaaf/pull/77))
- **That way out now pays the project's own provider again.**
  The install provider is the default that a project falls back on.
  A quiet secondary link opens it, so yaaf does not demand it of every seat.
  ([#80](https://github.com/dominicplouffe/yaaf/pull/80))
- **A project is a team at work. The wall of tabs is now a few pages, and each page answers one
  question.**
  The detail is inside the page, and not beside it.
  ([#61](https://github.com/dominicplouffe/yaaf/pull/61))
- **The home of a project is its Inbox, and the Team page now holds up with large teams.**
  ([#63](https://github.com/dominicplouffe/yaaf/pull/63))
- **Data and credentials are now two pages that a person can read.**
  Under Connections, each data source and each secret is one line.
  You change them in a drawer.
  An access map shows what can read what.
  Values are plain, and the health of a data source is in words.
  When you rotate a key, yaaf now keeps the grants that the key had.
  ([#64](https://github.com/dominicplouffe/yaaf/pull/64))
- **Setup & health advice is now plain and actionable, and yaaf does not repeat it.**
  A suggestion names the item to change, and when you put it away, it stays away.
  ([#60](https://github.com/dominicplouffe/yaaf/pull/60))
- **A brief draft now shows what it changes, and not the whole brief again.**
  ([#59](https://github.com/dominicplouffe/yaaf/pull/59))
- **An Operate project no longer has a delivery date, because its work repeats and does not finish.**
  ([#55](https://github.com/dominicplouffe/yaaf/pull/55))
- **Done, Approve and Noted now act on the first press, and the note is optional beside them.**
  A note on a Done used to be a second click, because you had to skip a text box.
  A refusal still asks why, because the CEO re-plans from that answer.
  ([fd848cc](https://github.com/dominicplouffe/yaaf/commit/fd848ccaf))
- **Each decision is now one button that names its outcome: Done, Raise to 60, Approve $199 or
  Noted.**
  An arrow beside the button serves a yes that needs words or a no that needs a reason.
  It opens a labelled box in place of the row.
  ([b869d56](https://github.com/dominicplouffe/yaaf/commit/b869d5683))
- **The "no" of an assist request is now called Not done, and not `Couldn't`.**
  In most cases you chose not to do the task, and you did not try and fail.
  ([978f84c](https://github.com/dominicplouffe/yaaf/commit/978f84cf5))
- **The README now says what the website says: build a company, hire a CEO.**
  ([#74](https://github.com/dominicplouffe/yaaf/pull/74))
- **The docs now say what the app does: one provider is chosen for the install, and roles carry the
  effort.**
  The sentences from the class era are gone from the project and integration guides.
  ([729a721](https://github.com/dominicplouffe/yaaf/commit/729a7218c))

### Fixed

- **Every click on the inbox now does something.**
  Approve and Deny sat on requests where the click changed nothing.
  Sixteen capacity approvals were granted, and none of them moved a dial.
  One desk filed the same raise three times.
  If you approve a capacity request, yaaf now raises that dial through the amendment path.
  Every button names its effect.
  A heads-up must quote the standing order that asked for it.
  ([fa464d4](https://github.com/dominicplouffe/yaaf/commit/fa464d45d))
- **What you write in one place is now read in every place.**
  A note that you left on an Inbox decision was answered by a work run in a box that you never open.
  Then a new chat session told you that it still waited on you.
  One note got two answers.
  The boardroom lines of the CEO and its work runs are separate processes, and they share no memory.
  The boardroom is now the record that all of them read.
  A note on any decision is your line there.
  The chat lane no longer uses notices that the planning lane needs.
  Every lane sees what the CEO said last.
  A new session no longer reads platform lines as its own words.
  ([8964567](https://github.com/dominicplouffe/yaaf/commit/896456740))
- **Every route that your words arrive by is now one path.**
  You can also write on twenty-five other decisions.
  They include a cut or reworded requirement, an audit, a review, a commitment, an overrun, a paused
  project, a stood-down team and an extended hypothesis.
  Before this change, those words reached the CEO only as a notice that expired after one day.
  Now every one goes through the same record.
  The test suite fails on any route that does not.
  ([b867fb7](https://github.com/dominicplouffe/yaaf/commit/b867fb714))
- **The boardroom chat now sees what still waits, and not a day of work that is already done.**
  Before this change, it saw the last 24 hours of notices on every turn.
  That was ten thousand characters.
  Nine of them said "say it in the boardroom now" for flags that the CEO's own runs had already
  raised.
  ([40086cd](https://github.com/dominicplouffe/yaaf/commit/40086cdd8))
- **yaaf now writes the answer to a note on a decision, and the note never waits for one.**
  Before this change, yaaf took the answer from the room: the first line of the CEO after yours,
  whatever its subject was.
  When the chat session was off, a note on a decision did not wake anything, so it could wait until
  something else woke the CEO.
  ([#75](https://github.com/dominicplouffe/yaaf/pull/75))
- **The boardroom no longer warns about a refusal that the CEO already got past.**
  ([#58](https://github.com/dominicplouffe/yaaf/pull/58))
- **When you move between pages, you no longer land at the bottom of the page.**
  ([#56](https://github.com/dominicplouffe/yaaf/pull/56))
- **Every link that threw "s is not a function" after the scroll reset now works again.**
  ([#57](https://github.com/dominicplouffe/yaaf/pull/57))
- **Every text colour now meets 4.5:1 on the background that it really uses.**
  A contrast audit of every screen in both themes found approximately 950 text nodes that failed.
  The muted grey failed on the sidebar and on tab strips.
  Avatar initials used chart-slot colours, down to 2.24:1.
  Finished, paused and gone rows became pale with opacity until the text inside them failed.
  The audit also found text at 11px.
  The worst case now clears 4.94:1.
  ([#68](https://github.com/dominicplouffe/yaaf/pull/68))
- **The same colours now clear 4.5:1 on hovered backgrounds too.**
  That covers a hovered sidebar row, the selected shortcut in the command palette, and muted text on
  a hovered card.
  The worst case is now 4.73:1.
  ([#69](https://github.com/dominicplouffe/yaaf/pull/69))
- **Two focus states that showed nothing now show a ring.**
  One is the "in your own words" box in the founding interview.
  The other is an item in a split menu.
  ([#70](https://github.com/dominicplouffe/yaaf/pull/70))
- **The disabled Send button is no longer blank, and no text is smaller than 12px.**
  Twenty-four rules were below the minimum that the type scale promises.
  They include the "lead" tag of the team and the dependency labels of the roadmap.
  ([#71](https://github.com/dominicplouffe/yaaf/pull/71))
- **The undo toast is now readable.**
  Its text colour pointed at a token that does not exist.
  In the dark theme, the result was white text on near-white.
  ([#65](https://github.com/dominicplouffe/yaaf/pull/65))
- **Nothing in the stylesheet now reads a colour token that nothing declares.**
  The same mistake drew every warning in NewProject and Settings in grey.
  The stylesheet test now refuses it, with or without a fallback.
  ([#66](https://github.com/dominicplouffe/yaaf/pull/66))

## [0.3.0] - 2026-10-21

**You must do nothing.**
The unread count starts at zero and does not count your history.
The upgrade marks each existing member as having read the boardroom up to the moment of the upgrade.
The update script applies the database changes.

**The count.** This release has 7 changes. The script `scripts/count_changes.sh` in the repository derives that count.

### Added

- **The boardroom now has an unread count.**
  Each member gets a read mark.
  The badge counts the boardroom messages after that mark that anyone else wrote.
  It shows on the project card and on each desk row.
  The mark moves only when you look at the tab or when you speak, and it never moves back.
  Slack has no read receipts, so a read in Slack alone does not clear the mark.
  ([85c272b](https://github.com/dominicplouffe/yaaf/commit/85c272b1d))
- **A confirmed brief now holds a change of direction.**
  When you change direction, the CEO writes it back to you as a brief.
  The brief gives the goal in plain words as it now stands, and every standing order with its number.
  It also gives what no longer applies, with your words quoted.
  If you confirm the brief, it replaces the founding goal.
  It also puts the orders in the prompt of every agent on every run.
  The brief lists the retired orders too.
  So an agent that finds the old rule in a file or a memory knows that it is dead.
  The CEO is told to rewrite the files, scripts and memories that still say the old thing.
  The brief lands on Decisions, and you can edit it there.
  Settings → Brief edits the same three parts.
  This change also corrects a shared read mark that let the chat session take your messages every two
  seconds, so work runs never saw them.
  ([e26279f](https://github.com/dominicplouffe/yaaf/commit/e26279f38))

### Changed

- **The inbox is now quiet when it is empty, and the health card names the seats that never run.**
  An inbox at zero is one line, and not a banner.
  The health card and the Team page name a seat with no run in a week.
  The header badge says "problem" or "suggestion", and it opens Setup & health.
  Each person can put it away.
  ([#52](https://github.com/dominicplouffe/yaaf/pull/52))
- **The CEO now writes for a reader with no context.**
  Everything that a person reads must stand on its own.
  That includes boardroom messages, questions, approvals, flags and reviews.
  Each item must name things by what they are, and not by an ID or a platform word.
  Each number must come with what it is measured against.
  Times must be in your own timezone.
  A decision must give the situation, why it matters, the options, the recommendation, and what
  happens if you say nothing.
  ([#54](https://github.com/dominicplouffe/yaaf/pull/54))
- **A deliverable that the boardroom announces now opens the file itself.**
  The name links straight to the preview of that file, and does not tell you that it is on the
  Deliverables page.
  ([8219224](https://github.com/dominicplouffe/yaaf/commit/821922412))

### Fixed

- **A wait that ends by itself now raises no banner.**
  A scheduled start, pacing, the wake ceiling and batching are now quiet.
  The Overview shows a "resumes in …" line, and not a health finding.
  ([#53](https://github.com/dominicplouffe/yaaf/pull/53))
- **yaaf now announces a published deliverable in the boardroom, and not only in Slack.**
  Before this change, publishing wrote to the Slack channel and never to yaaf's own boardroom.
  So Slack knew something that the boardroom did not.
  ([a2abfbe](https://github.com/dominicplouffe/yaaf/commit/a2abfbe8a))

## [0.2.0] - 2026-10-14

**You must do three things when you upgrade.**

1. **Sign in with an account. The static admin token is gone.**
   Nothing reads `YAAF_ADMIN_TOKEN` now.
   Set `YAAF_ADMIN_EMAIL`, or pass `--admin-email`.
   The deploy or update script then makes that person the instance admin, and it prints a one-time
   link to set the password.
   Invite everyone else from Settings.
   yaaf refuses a script that sends the old token.
   Log in with `yaaf login`, or give the script a personal token in `YAAF_TOKEN`.
2. **Call the single-server scripts from `deploy/`.**
   They moved beside the compose file that they drive.
   `./update_single_server.sh` is now `deploy/update_single_server.sh`, and the deploy and kill
   scripts moved the same way.
   Change any cron entry or note that names the old path.
3. **Set `YAAF_PUBLIC_BASE_URL` if you serve yaaf over https.**
   It is now in `deploy/.env.example`, and not only in the deployment doc.
   It makes session cookies `Secure`, and MCP discovery advertises it.
   If you change it later, the MCP audience changes, so you must connect again the connectors that
   you already added.

The update script applies the database changes for you.

**The count.** This release has 11 changes. The script `scripts/count_changes.sh` in the repository derives that count.

### Changed

- **People sign in.**
  The dashboard and the CLI now authenticate a person, and not a client.
  They give accounts with passwords, browser sessions, personal tokens for scripts, and per-project
  roles with co-chairs who can decide.
  MCP connectors are the same, and MCP tokens work as they did.
  ([#47](https://github.com/dominicplouffe/yaaf/pull/47))
- **The single-server scripts are now in `deploy/`,** beside `_common.sh` and the compose file that
  they drive.
  Every doc and every hint on the screen names the new path.
  ([49c6e4d](https://github.com/dominicplouffe/yaaf/commit/49c6e4ddb))
- **`/app` now keeps the scheme of the browser behind a TLS proxy.**
  Behind a proxy that ends TLS, `/app` used to redirect to `http://host/app/`.
  That is the plain-http hop that a browser marks "Not Secure", and the redirect used the scheme that
  the server sees.
  `/app` now answers with a relative path.
  `deploy/.env.example` lists `YAAF_PUBLIC_BASE_URL`.
  Without that value, an install got cookies that were not `Secure`, and MCP discovery named
  `http://api:8080`.
  ([c9a2754](https://github.com/dominicplouffe/yaaf/commit/c9a2754ba))
- **Any harness and any provider now obey the same rules.**
  A seat can run on the Claude Agent SDK or the Claude CLI.
  It can also run on yaaf's own loop against the Messages API or any OpenAI-compatible server.
  A seat can run on the Codex CLI, the Gemini CLI or OpenCode too.
  yaaf meters every one of them against the one cap of the project.
  The boardroom runs on the CEO's own seat.
  ([#45](https://github.com/dominicplouffe/yaaf/pull/45))
- **The server now serves the Deliverables list.**
  The tab used to walk the whole workspace and send every file to the browser every 15 seconds for
  each tab.
  On a large project, the polls piled up and the tab stopped loading.
  A per-project index now answers search, filters, sort and day folders.
  The screen fetches only the rows that it shows.
  A stale reply can no longer overwrite a newer one.
  Responses are compressed, but file downloads are not.
  ([#46](https://github.com/dominicplouffe/yaaf/pull/46))
- **The project pages are rebuilt around three questions: what needs you, who does what, and whether
  the work is on track.**
  The pages give one Inbox, a live org chart, and an Overview that you can read in five seconds.
  ([#49](https://github.com/dominicplouffe/yaaf/pull/49))
- **A deliverable can now be 100 MB, and not 5 MB.**
  The old limit refused a ten-minute podcast mp3 at 9.5 MB.
  This is a trial.
  An upload is still one base64 request body, and downloads are still buffered.
  Because of that, a large file takes longer to publish, and it holds memory during the upload.
  ([435a210](https://github.com/dominicplouffe/yaaf/commit/435a21023))
- **A new model key now reaches the boardroom.**
  The chat session of the CEO used the credential that it started with for days.
  You replaced an exhausted key with a funded one, and the boardroom still answered "session limit"
  while every work run paid with the new key.
  The session now records which credential pays it, and it restarts when that credential changes.
  ([de196df](https://github.com/dominicplouffe/yaaf/commit/de196df9c))
- **The docs now say what the product is.**
  The history of the project starts on 14 August 2026.
  The docs give the monthly budget as a plan that needs an account when you cross it.
  The wall is above that plan, and the docs do not call the plan the wall.
  ([#48](https://github.com/dominicplouffe/yaaf/pull/48))

### Added

- **There is now an agent inbox, on the screen, in the CLI and over MCP.**
  What the agents ask you for has its own view.
  Your own chat bubble is readable, and it is not low contrast.
  The inbox banner says in plain words what it is about.
  ([#51](https://github.com/dominicplouffe/yaaf/pull/51))

### Fixed

- **yaaf now says when a model key does not cover the models of the team, and it offers the way out.**
  The founding flow used to accept a key for one provider when the roles named models on another
  provider.
  It failed only at the end.
  ([#50](https://github.com/dominicplouffe/yaaf/pull/50))

## [0.1.1] - 2026-10-07

**You must do nothing.**
This release changes no database schema and adds no settings.

**The count.** This release has 4 changes. The script `scripts/count_changes.sh` in the repository derives that count.

### Changed

- **Every seat moves to the 5.5 generation, and Opus costs approximately a fifth less.**
  All roles and defaults now name Opus 5.5 and Sonnet 5.5.
  Opus 5.5 lists at $4 and $20 per million tokens.
  Opus 5 lists at $5 and $25.
  Sonnet 5.5 has the same price as Sonnet 5.
  yaaf now moves a high-stakes seat on Opus 5 to 5.5, and does not keep it on the more expensive
  model.
  ([43b315c](https://github.com/dominicplouffe/yaaf/commit/43b315c7e))

### Fixed

- **A sign-in to an MCP connector now survives a change of address.**
  A registration binds a client to the callback that it named.
  yaaf used to register only when it had no client on file.
  The hostname of the install can change, for example to a new Tailscale name.
  After such a change, every sign-in was refused with `invalid_redirect`, and nothing on the yaaf side
  said why.
  yaaf now records the address that each client was registered for, and it registers again when that
  address moves.
  It never replaces a client that you pasted in by hand.
  ([1cd1747](https://github.com/dominicplouffe/yaaf/commit/1cd1747a1))
- **The settings rail now lines up, and the selected item is the bold one.**
  The items were centred under headings that were left aligned.
  Every idle item was heavier than the selected one, because a global button rule had more weight than
  the rule of the rail.
  ([56a4d55](https://github.com/dominicplouffe/yaaf/commit/56a4d5594))
- **The CEO can now put a team task on the board.**
  yaaf said that every field of the task tool was necessary.
  A team task must omit the parent, the assignee and the hypothesis.
  yaaf refused the call when they were omitted, and it refused the call again when they held empty
  values.
  One desk spent eight refusals on this over two runs, and left 26 accepted requirements with no task
  on the board.
  A field that a call can omit is now optional.
  This also unblocks data-source discovery and the removal of a stand-down condition.
  ([d6878e5](https://github.com/dominicplouffe/yaaf/commit/d6878e585))

## [0.1.0] - 2026-09-20

The version the repository was at when this changelog begins, tagged on the last commit before
1 October 2026 ([b588a33](https://github.com/dominicplouffe/yaaf/commit/b588a33)). Its release notes
are not in this file.

## Notes on method

**The week.** Seven days, Thursday to Wednesday, counted from Thursday 1 October 2026: 1–7 October,
8–14 October, and so on. Each release is dated by the last day of its week. The cut is made in UTC,
because the public timestamps of a merge are UTC. Those timestamps are the merge time of the pull
request and the date that a GitHub Release carries. So anybody who checks the count against GitHub
gets the same week boundaries as this file.

**What counts as one change.** One pull request merged into the default branch during the week is one
change. One direct commit to the default branch that did not come through a pull request is one
change, and its SHA is the link in its entry. These count as zero: merge commits themselves (the pull
request they merge is counted once, for the pull request), reverts of something merged in the same
week, and changelog or version-bump commits made by this project.

**The version.** Six or more changes: the minor version goes up by one and the patch returns to zero
(0.2.4 becomes 0.3.0). Five or fewer, including exactly five: the patch goes up by one (0.1.0 becomes
0.1.1). A week with no changes gets no release and the version does not move.

**Where the numbers come from.** `scripts/count_changes.sh` in the repository derives every week's
count from `git log --first-parent` alone. It needs no token, no network and no API quota. It
classifies each entry as a pull request or a direct commit from the commit message of that entry. It
prints the version chain, with the counted PR numbers and commit SHAs under each week. Anybody can
re-run it against a clone and get the same numbers. The count was also checked one time against the
list of merged pull requests in GitHub. For each of the five weeks, the PR numbers of the script are
the same PR numbers that GitHub records as merged into `main`. Every entry that the script reports as
a direct commit returns no associated pull request from GitHub.

**Two changes move a week depending on the time zone, and no version moves.** PR #52 was merged at
2026-10-15T01:02Z and PR #81 at 2026-10-29T00:04Z. Read in the chairman's Eastern time they land in
the previous week, which would make the weekly counts 4, 12, 6, 35 and 22 instead of 4, 11, 7, 34 and
23. Under either cut every week except the first is still six or more, so the version chain is
identical: 0.1.1, 0.2.0, 0.3.0, 0.4.0, 0.5.0. The 1–7 October week has the same four changes under
both.

**One entry per change.** Every counted change appears exactly once above, under exactly one kind, so
a week's count can be checked by counting the entries in its section. Where one change would fit two
kinds it is filed by what an operator notices most. Where a change was replaced later in the same
week, both entries are kept and each describes what that change did when it landed, so the arithmetic
of the week stays visible. The token conversion of #81 was replaced by #82, and the seat editor had
three passes in #76, #77 and #80. The last of them states the behaviour you end up with.

**Database changes.** The commit that carries this file also sets the version string in
`pyproject.toml` and `yaaf/__init__.py` to the newest tag it publishes, 0.6.0, so an installed
package reports the same version as the release it came from. Every release from 0.2.0 on adds
database migrations. `deploy/update_single_server.sh` applies them on a server and `yaaf up` applies
them on a desktop install. An install driven by hand needs `alembic upgrade head`. No migration in
these five weeks deletes rows when it is applied. Six of them rewrite rows that are already there.
This is what each one does to your rows. 0046 lower-cases the stored user emails, because sign-in now
matches on them exactly. It also gives every existing project's chairman the membership row that
creation writes today (0.2.0). 0048 stamps existing members as having read the boardroom up to the
upgrade, so the new badge does not count your history (0.3.0). 0054 fills in `secrets.updated_at`
from `created_at` where it was empty (0.4.0). 0055 renames the stored harness names to `yaaf_loop`.
It also pins the CLI that an OpenAI or Google API key used to drive, so no seat changes engine on
deploy (0.4.0). 0063 moves a cut obligation back to the cycle it was cut in and re-says the old cycle
scorecards (0.5.0). 0064 re-counts the "Met since last" figure frozen in each meeting (0.5.0).

**The words.** Every entry is written in ASD-STE100 Simplified Technical English. The sentences are
short and each one carries one idea. The voice is active. There is no semicolon and no contraction.
An interface label quoted in backticks keeps its exact spelling, because it is a name and not prose.
`Couldn't` in the 0.4.0 section is a former label of that kind, and it stays.

[0.6.0]: https://github.com/dominicplouffe/yaaf/releases/tag/v0.6.0
[0.5.0]: https://github.com/dominicplouffe/yaaf/releases/tag/v0.5.0
[0.4.0]: https://github.com/dominicplouffe/yaaf/releases/tag/v0.4.0
[0.3.0]: https://github.com/dominicplouffe/yaaf/releases/tag/v0.3.0
[0.2.0]: https://github.com/dominicplouffe/yaaf/releases/tag/v0.2.0
[0.1.1]: https://github.com/dominicplouffe/yaaf/releases/tag/v0.1.1
[0.1.0]: https://github.com/dominicplouffe/yaaf/releases/tag/v0.1.0
