# 03 — A desk the world wakes up

> **Why not just a Claude Skill?** Because a skill has nobody listening. This
> project has a **signed HTTP endpoint**: your app, a form, a cron job or your
> own `curl` POSTs an event, and a named agent wakes up and works it — at 03:00,
> without you, with the wake still passing through every rate brake so the world
> can knock but never stampede.

**What you get:** a standing triage desk. Things happen; the desk finds out,
works them, and keeps one living register that collapses repeats instead of
growing forever.

**What it teaches:** external wakes, attested evidence, and memory that has to
survive months.

**What you need:** your Anthropic API key. The endpoint and its signing secret
are created *with the project* — no service to sign up for.

**Cost:** capped at $50/month.

---

## Import it

**+ New project → Start from a setup file →** `03-a-desk-the-world-wakes.json`,
then say what you are actually watching.

**Then keep the screen that appears after you click Create.** It prints two
webhook URLs and their secrets, and a worked `curl`. That is the whole
integration — copy it somewhere before you navigate away. (It is also in
Settings → Integrations, and both secrets can be regenerated there.)

## Send it something

The creation screen gives you this, filled in with your real project id and
secret:

```bash
BODY='{"description":"checkout returned 500 for user 4471, stack trace attached",
       "source_url":"https://your-logs.example/abc123",
       "wake":"Nadia"}'
SIG=$(printf '%s' "$BODY" | openssl dgst -sha256 -hmac "<generic secret>" | cut -d' ' -f2)
curl -X POST "http://localhost:8080/webhooks/generic/proj_…" \
  -H "Content-Type: application/json" -H "X-Yaaf-Signature: $SIG" -d "$BODY"
```

Three fields matter:

- **`description`** — what happened, in words. This becomes evidence.
- **`wake`** — `"ceo"` or an agent's exact name. That agent is woken *urgently*
  rather than at its next heartbeat. Omit it and the event is simply recorded.
- **`revenue_usd`** — optional, and the only way money can ever be booked as
  income. There is **no agent tool that writes revenue**, so a number here
  cannot be invented by an agent having a good day.

Now watch **Records → Runs**. A run appears within seconds, its wake reason is
the webhook, and Nadia is reading your event.

## Why signed, and why that matters

Evidence that arrives this way is **platform-attested**: the platform saw the
signature, so no agent typed it in. That is a different class of fact from an
agent telling you something happened, and the Work tab labels it differently.

The signature is HMAC-SHA256 over the raw body. An unsigned or wrongly-signed
caller learns nothing — the signature is checked before the body is even parsed.
The secrets never enter a sandbox, so no agent can forge its own evidence.

## The register is the real test

The goal asks for **one row per distinct problem**, with a count and
first/last-seen — never a second row for a repeat. That is deliberately the hard
part, and it is where a stateless assistant falls over: on the fortieth event,
knowing whether this is the same underlying thing as event nine requires
something that remembers event nine.

Here that is project memory with a curator and a hard cap: at the cap, adding is
**refused** until the CEO compacts (folds several entries into one, freeing
slots) or forgets. Nothing is silently evicted, and compaction is reversible —
folded entries stay readable with `include_superseded`.

## What to watch

- **Duplicate rows in the register.** The single most likely failure. If event
  40 gets its own row, say so in the Boardroom — it is a real defect.
- **Theorising before reproducing.** The `support` charter says corroborate
  first. A register full of hypotheses is a worse artifact than a short one full
  of confirmed facts.
- **Wake storms.** Fire twenty events in a minute on purpose. You should see the
  wake ceiling hold, the runs spread out, and the Overview say why — not twenty
  runs at once and a hole in your budget.

## Try this next

- **Point your app at it.** A `try/except` that POSTs the exception is ten lines
  and turns this into something you actually keep.
- Send one with **`revenue_usd`** and watch it appear in the ledger as income
  the CEO is told about, excluded from the budget sums — a project is never
  paused for earning.
- Then **[04 — An overnight crew](04-an-overnight-crew.md)**, which nobody is
  awake for either.
