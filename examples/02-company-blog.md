# 02 — A blog that publishes weekly and knows whether anyone read it

**You want:** two useful posts a week on your company blog, researched, in your
voice, ready for you to approve. **You don't want:** an AI content farm that
fills your sitemap with plausible mush and reports "8 posts published" as if
that were a result.

The distinction the platform enforces: publishing is an *activity*. A reader is
a *result*. Your blog runs on a domain you control, so nothing you publish can
count as external evidence — only what comes back can.

## The org

| Team | Role | Members | Does |
|---|---|---|---|
| Content | `blog-writer` | Jules | Drafts, publishes to WordPress as drafts, records the URL |
| Research | `researcher` | Sam | Question selection, sources, competitive reading |

One writer. Two writers on a blog produce two voices and a memory fight over
which one is the house style.

## The goal

> Grow qualified inbound traffic to acme.dev by publishing two posts a week
> that answer specific questions our buyers — heads of platform engineering at
> 50–500 person SaaS companies — actually search for. Every post is a WordPress
> draft for the chairman to approve; nothing goes live without that. Establish
> and record the house voice in the first post and keep to it. Success is
> external response: referral traffic from uncontrolled domains, comments,
> shares we can point to, and inbound mentions — not post count. Reassess the
> topic strategy after 8 published posts.

Note what's carried in there: the audience is specific enough to disqualify a
topic, the approval gate is stated, the voice is a first-class deliverable, and
the success measure is response rather than output.

## Wire WordPress

Settings → **Variables & credentials**. Add one secret:

| Field | Value |
|---|---|
| Name | `wordpress` |
| Value | `jules:xxxx xxxx xxxx xxxx xxxx xxxx` (a WordPress **application password**, not your login) |
| Roles | Marketing Blog Writer only |

The name becomes the env var `WORDPRESS` inside that role's sandbox, and only
that role's. The researcher never sees it. Neither do you, in chat — the value
is encrypted and injected per run.

From the CLI, if you prefer:

```bash
yaaf set-secret wordpress --project acme-blog --scopes blog-writer   # value is prompted, never echoed
```

The `blog-writer` role knows what to do with it: record the side effect first
with a stable key (`wp:acme.dev:<post-slug>`), then POST to
`/wp-json/wp/v2/posts` with `status: draft`. If a run is retried after a crash,
the ledger says that post already fired and it does not publish twice.

## Controlled domains — read this twice

```
acme.dev
www.acme.dev
blog.acme.dev
```

Now the important consequence: **a published post on your own blog is internal
evidence.** It proves the work exists. It does not prove the work worked.

The signals that *do* count as external: a comment from someone unaffiliated,
a link from a domain not on your list, referral traffic with a named source, a
share you can point to. The role prompt says it plainly — "report observed
numbers or say 'not measured'; never estimate."

**Tuning note:** organic response builds over weeks, so set `drought_hours` to
168 (below) and let the team build the engine before the signal arrives. When
it does arrive, it's a real reader — which is the number you actually wanted
all along.

## Success tests for writing

Prose work is where success tests get vague, so hold the line:

```json
{"type": "observable", "target": "WordPress draft exists at acme.dev/?p=<id> with title, meta description, and target query, 900+ words, every factual claim carrying a source URL"}
{"type": "observable", "target": "Post answers the query 'how to rotate database credentials without downtime' in the first paragraph, verified by reading the rendered draft"}
```

Refused: `"target": "high-quality post about credentials"`. Length alone is a
weak test — pair it with the reader-facing claim, as above.

## Constitution settings that matter here

| Setting | Default | For this project | Why |
|---|---|---|---|
| `monthly_usd_cap` | 1000 | 120 | Two posts a week with research is not expensive |
| `queue_ceiling` | 8 | 4 | Half-written posts are worth nothing; finish before starting |
| `drought_hours` | 24 | 168 (7 days) | Organic response takes weeks; don't punish month one |
| `external_ratio_applies_from` | 3 | 8 | Let the first 8 tasks build the engine before the ratio bites |
| `memory_cap_team` | 40 | 40 | The house voice lives here — leave room |

## What the first week looks like

- **Day 1** — CEO states the bet ("posts targeting operational how-to queries
  will pull qualified traffic; kill if 8 posts produce zero uncontrolled
  referrals by day 45"), and creates a voice-definition task first.
- **Day 2** — Jules proposes the voice, records it as a team memory, and drafts
  post one. It lands in WordPress as a draft.
- **Day 2, evening** — you read it. The bones are right, the intro wanders. You
  say so in the Boardroom: *"Voice is close. Cut the throat-clearing intro —
  start on the answer. Nothing ships with a sentence that begins 'In today's
  fast-paced'."*
- **Day 3 onward** — the correction is in team memory, and post two is better.

That loop — read the draft, say one specific thing to the CEO — is the entire
job. It's worth more than any prompt edit you could make.

## Your job

- **Press publish.** The draft in WordPress is the deliverable; your click is
  the approval. Refuse to make this automatic. It's the last honest gate
  between an agent and your company's public voice.
- **Feed back specifics.** "Too generic" changes nothing. "Cut the intro, start
  on the answer, one example per section" becomes a team memory.
- **Spot-check the sources.** `yaaf audit acme-blog --n 5`, then open two cited
  URLs and check they say what the post claims. This is the single highest-value
  five minutes you'll spend on the project.
