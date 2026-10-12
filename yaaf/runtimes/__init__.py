"""What a seat runs on: the provider, the credential kind, the harness, the model.

Four facts that used to be one word (`runtime`) and one vendor. They are kept
apart here so that a second provider is a registry entry rather than an edit to
every file that ever assumed the first — see docs/RUNTIMES.md for the design,
the rules it adds, and the milestones.

- `providers` — who sells the tokens: credential kinds, the variable each is
  injected as, what a malformed one looks like, what a dead one says.
- `catalog` — the models: which provider serves each, and how they rank.
- `harnesses` — what runs the agent loop, and what each can do. Imports nothing
  from `yaaf`, so the sandbox can read the same copy the control plane does.
- `seats` — the one place a seat's model, effort, provider and harness are
  resolved, so the runner, the chat lane and the spend report cannot disagree.

None of this decides what an agent may do. That stays in the 4xx answers of
`/agent/*`, on every harness.
"""
