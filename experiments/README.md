# experiments/

Specs for platform experiments. The only one that matters right now is
`m2.spec.json` — the M2 baseline experiment (docs/M2_PROTOCOL.md), the gate the
whole org-framework bet stands or falls on.

## Running M2

```
# 0. Chairman: pick 2-3 goal pairs in m2.spec.json, set budgets/window/weights,
#    then freeze protocol + spec in a commit. Nothing runs before that commit.
yaaf experiment init experiments/m2.spec.json     # provisions both arms per pair, paused
yaaf start m2-g1-oss-tool-single                  # start both arms of a pair
yaaf start m2-g1-oss-tool-org                     #   ...within 24h of each other
yaaf experiment status experiments/m2.spec.json   # live E / C / T / conversion table

# window closes:
yaaf experiment export experiments/m2.spec.json --out m2-bundles
yaaf experiment judge m2-bundles                  # 3 judge-model passes per blind bundle
yaaf experiment score m2-bundles <label> <f> <c> <u> <co>   # chairman's blind pass, per bundle
yaaf experiment decide experiments/m2.spec.json --bundles m2-bundles
```

`decide` unseals the label→arm mapping, computes the preregistered composites,
applies the decision rule from the protocol (§7), and writes `docs/M2_RESULT.md`.
Do not open `m2-bundles/mapping.sealed.json` before your scores are recorded —
the blinding is the point.

Arm construction: the org arm is CEO + a 3-agent team (lead + 2); the single
arm is one CEO-kind agent that is also the sole member (hence derived lead) of
its only team, so one brain plans *and* executes under byte-identical policy.
