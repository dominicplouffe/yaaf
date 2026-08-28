# The goal grammar

The rubric the Founder writes against and the judge grades against lives with
the code that reads it, at
[`yaaf/founder/GOAL_GRAMMAR.md`](../yaaf/founder/GOAL_GRAMMAR.md).

That is not filing tidiness. It is read **at runtime** — by the Founder's
system prompt and by the goal grader — and the deployed image ships `yaaf*`
and nothing else, so a copy in `docs/` would be a file the server cannot open.
Keeping one copy is also the rubric's own rule: two copies drift, and then the
critic starts failing goals the author was never told about.
