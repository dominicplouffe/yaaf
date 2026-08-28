# The goal grammar — what separates a goal that works from one that putters

This is the rubric the Founder writes against and the judge grades against. It
is **not a form**. A goal is prose, and the best goal in this repository is
prose. What follows is what that prose has to *contain*, and why each element
earns its place.

Read it as a checklist for a critic, not as a template for an author. A goal
that covers all eight elements in four paragraphs beats one that has eight
labelled sections.

## Who this is for

Three readers, one file:

- **The Founder** (`yaaf/founder/`) drafts against it and asks the chairman
  only for what it cannot infer.
- **The judge** (`evals/judge.py`) grades a draft and names what is missing.
- **A human or an outside agent** authoring a bundle by hand.

Keeping one copy is the point. Two copies drift, and then the critic starts
failing goals the author was never told about.

---

## The eight elements

### 1. A finish line, and the record that settles it  `finish_line`

Not "get customers". Not "grow revenue". A condition that some specific
artifact either shows or does not show, named precisely enough that the project
cannot argue its way past it.

> *"THE FINISH LINE is revenue booked through the payment processor's signed
> webhook — the platform's own record, not a claim in a status report."*

The second half is the part that does the work. Every goal names *what* should
be true; a good one also names **where you go to find out**, and picks a record
the project does not author itself. A finish line the project can write into
its own status report is not a finish line.

**Failing:** "Launch the product and get traction."
**Passing:** the quote above.

### 2. Anti-metrics — what explicitly does not count  `anti_metrics`

Name the plausible substitutes for success and rule them out, because a project
under pressure will find them.

> *"Signups, waitlists, traffic, stars and compliments are leading indicators,
> and this project is not graded on leading indicators."*

Without this, a project that cannot reach the finish line will quietly
re-aim at the nearest thing it *can* reach, and report that instead. This is
the single cheapest element to add and the one that prevents the most drift.

### 3. Method constraints — how the work must be done  `method_constraints`

Where the *approach* matters and a competent agent might reasonably choose
otherwise, say so, and say it as a rule with a shape.

> *"PHASE ONE IS A BAKE-OFF, NOT A BRAINSTORM. Produce five candidate ideas and
> research all five to the same depth before ranking any of them: [eight
> specific things]. Then choose one and write down why the other four lost."*

Note what makes this work: it is falsifiable. "Do good research" is not a
method constraint. "All five to the same depth, then write down why four lost"
is — you can look at the output and see whether it happened.

Only constrain method where you actually care. A goal that scripts every step
has stopped being a goal and become a task list, and it will be wrong by week
three.

### 4. A bias, marked as a bias  `stated_bias`

State preferences as preferences, with standing permission to override them on
evidence.

> *"A BIAS, NOT A RULE: prefer something a consumer or prosumer buys on a card
> at a self-serve price… If the strongest idea is the enterprise one, take it
> and argue for it — the ranking is on evidence, not on the chairman's taste."*

This element exists because the alternative failure modes are both bad: an
unstated preference gets violated and the chairman is annoyed; a preference
stated as a hard rule gets obeyed past the point of sense. Marking the tier
explicitly is what lets an agent do the right thing when reality disagrees with
you — and know that it is allowed to.

### 5. A quality bar, stated as a standard  `quality_bar`

If "done" and "good" are different things here, say what good is, and say who
owns it.

> *"Functional-but-ugly is not a milestone on the way to good — it is the
> version that gets no second visit and teaches you nothing about whether the
> idea works, because you never find out whether they refused the product or
> refused the presentation."*

The reasoning is doing real work: it converts taste into a *consequence*, which
is something an agent can act on. A bar with a reason survives contact with a
deadline; "make it look nice" does not.

Where a bar exists, name its owner and hold everyone to it — and say that it
constrains ambition rather than licensing gold-plating, or it becomes an excuse
to never ship.

### 6. The human's role, bounded — including what they will refuse to do  `human_role`

The chairman is not a member of the project. Say what they will supply, through
what channel, and — the part people forget — **what they will not do**.

> *"Distribution is this project's job. The chairman will not post about it,
> will not email his contacts, and will not introduce you to anyone… for this
> purpose he does not have one."*

> *"ASK HIM FOR EXACTLY WHAT YOU CANNOT GET YOURSELF, and no more… If you can
> sign yourself up for a service and pay for it inside the spend envelope, do
> that instead of asking."*

Without the refusals, a project will plan around resources you never agreed to
provide, and you will discover this at the worst moment. The negative space is
more informative than the positive space.

### 7. Resource posture — with its reason and its release condition  `resource_posture`

Say what the project gets, *why* it is set there, and what would change it.

> *"MONEY AND TIME ARE BOTH SHORT ON PURPOSE. The model budget is small while
> you are still deciding what to build, because a project that spends its year
> on research never ships; it will be raised at a review once there is
> something in the market that feeding would help."*

A bare number reads as an arbitrary constraint to route around. A number with a
reason and a release condition reads as a strategy, and an agent can act on a
strategy — including by earning the raise.

### 8. The failure deliverable  `failure_deliverable`

What is owed if the finish line is not reached. This is the subtlest element and
almost nobody writes it unprompted.

> *"If 31 December arrives and nobody has paid, the deliverable is an honest
> post-mortem: which ideas died and on what evidence, what was actually built
> and shipped, what was tried in the market and what it returned, and what would
> have to be different for this to work. That is a real result and it will be
> read as one. What will not be accepted is a year of documents about a product
> nobody outside this project ever saw."*

Two things happen when you write this down. A project that is going to miss
knows what to do with its last weeks instead of hiding, and — because the
failure deliverable is itself demanding — it removes the incentive to fake the
success one. A goal without it quietly implies that failure is unspeakable,
and projects that cannot speak about failure produce documents instead.

---

## How to grade

Each heading above carries the element's key in backticks. Those keys are the
grader's contract — it reports a verdict per key — so they are written here
rather than kept in the code alone.

For each element, one of:

- **present** — the goal covers it, specifically enough to act on;
- **weak** — gestured at, but not falsifiable, or missing the half that does
  the work (a finish line with no settling record; a bias not marked as one;
  a budget with no reason);
- **missing** — absent.

A grade is only useful if it names the fix. "No anti-metrics" is a finding;
"consider adding more detail" is not. Quote the offending line where one exists,
and where none exists say what the goal is silent about.

**Not every element applies to every project.** A one-week Deliver project with
a fixed artifact does not need a bias or a resource-release condition, and
saying so is a valid grade. What is very rarely optional is `finish_line`,
`anti_metrics` and `failure_deliverable` — those three are what stop a project
from grading itself.

## What a grade is not

It is not a style review. Long is fine; the reference goal is 4,700 characters.
Short is fine too, if it covers the ground. Do not ask for structure, headings,
or shorter sentences — ask for **content that is missing**, and let the author
write it however they write.

## The worked example

`docs/projects/idea-to-customer.json` carries the goal quoted throughout this
file. Read it whole before drafting one; it is the standard, and it is the
regression test (`evals/`) that the Founder is measured against.
