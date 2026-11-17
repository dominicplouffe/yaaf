# Brief: bring yaaf's UI onto the new brand

You are refactoring the yaaf web app (`web/`) and its desktop surfaces to the new brand identity. Brand assets and rules are in `docs/brand/`. Read this whole brief before you change anything.

## The idea you are expressing

In yaaf, **nothing counts until someone who didn't make it checks it**. The logo shows this. In the wordmark, the second *a* is italic and set in "Seal" crimson: it is the checker's signature. The bookkeeper's **double rule** means "final, checked." The UI should let a reader see who made a result, who checked it, and what it cost, without a hunt.

## Read first (in this order)
1. `docs/brand/reference/colors.md`: palette, rules, theme mappings and measured contrast. **Rule 1 is the most important rule in this brief.**
2. `docs/brand/reference/typography.md`
3. `docs/brand/integration/README.md`: a drop-in theme that was already built and tested against this repo.
4. `docs/brand/screens-target/`: the real app with that drop-in theme applied, light and dark. This is your **baseline target**, not the ceiling.
5. `docs/brand/reference/brand-guidelines.pdf`: pp. 13–19 (graphic language, icons, layout, motion, product).
6. `CONTRIBUTING.md` and `CLAUDE.md` in the repo root. Their house rules and test tiers apply to you.

## Non-negotiable rules
1. **Seal (`#B8203A` light, `#EE4259` dark) is the checker's signature, and nothing else.** Use it for the italic *a* in the logo and for the name or line of whoever checked a result.
   - Never for a status: "verified" stays `--good` green.
   - Never for an error: errors use `--critical`.
   - Never for a button, link or decoration.
   - If you're unsure, it isn't Seal.
2. **Primary actions are ink.** Links, selection and focus rings are Registrar Blue (`--accent`).
3. **One red.** Never put the light Seal and the dark Seal next to each other.
4. **Status is never colour alone.** It always has an icon or a word. (This is already a stylesheet rule; keep it.)
5. **Contrast:** every text colour clears 4.5:1 on page and surface, in both themes. **12 px is the floor.**
6. **Chart slots `--c1…--c5`** keep their fixed order. Use the new values from `tokens.css`; they were re-tested for colour-vision separation.
7. **Dark mode is neutral Carbon (`#121316`), never navy.** No gradients, glows, glass, mascots or robot imagery.
8. **Never retype the wordmark** in a font. Use the SVG (`Wordmark.tsx`).
9. **Behaviour, API, server, copy and information architecture don't change.** This is a visual refactor. The only allowed copy change is "YAAF" → "yaaf". If you think a flow or a string should change, list it in the PR. Don't make the change.
10. **Don't rename "chairman"**, even though brand copy uses "chair". That is the maintainer's decision.

## Work plan (one PR per phase, small and single-purpose)

### PR 1: foundation (mechanical)
- **Fold the theme into the stylesheet itself.** Don't keep `brand-theme.css` as an override. Port its values into `web/src/styles.css` (dark `:root`, the light `@media` block and `:root[data-theme="light"]`) under the existing variable names, then delete the override. Update the stylesheet's header comment.
- **Fonts.** Swap them per `integration/yaaf-web/main.tsx.fonts.diff`:
  - Public Sans for UI
  - Brygada 1918 600 and 600-italic for display
  - keep the existing system mono stack for `--mono`
- **Logo.** Add `Wordmark.tsx`. Use it in `shell.tsx` (sidebar, height 24), `pages/Login.tsx` and `pages/Setup.tsx` (h1, height 44, with one x-height of clear space).
- **Favicon.** Replace `web/public/favicon.svg` with `docs/brand/favicon/favicon.svg`. Set `<meta name="theme-color">` per colour scheme, following `favicon/head-snippet.html`.
- **Tray.** Replace `ICONS` in `yaaf/cli/indicator.py` with `integration/yaaf-tray/indicator_icons.py`. Point `yaaf/cli/desktop.py` at `docs/brand/tray/hicolor-app-yaaf.svg`.
- **Casing.** "verified by YAAF" → "verified by yaaf", everywhere in UI strings. Golden evals may change: re-record them and say so in the PR (CONTRIBUTING rule 3).

### PR 2: brand moments in the product (the real design work)
Each change is CSS-first. Make a component edit only where CSS can't reach. For any new pattern, follow CLAUDE.md's "match its sibling's footprint" check.

1. **Made by / checked by.** Wherever a result or deliverable appears (Results list and "Why you can trust" panel, Deliverables, inbox sign-offs, the chat's "What they did"), show the pair consistently:
   - The maker in normal weight.
   - The checker's line in Brygada italic, in Seal.
   - The check's status mark keeps its status colour.

   The Results trail already does this through CSS (`.rs-mark.verified|judged + .rs-step-body .rs-step-t`). Extend the same treatment to the other surfaces.
2. **The double rule** (two 2 px ink rules with a 2 px gap) marks only "final, checked". Use it where an evidence trail ends, under a signed-off delivery, and under a completed review. Never use it as a generic divider.
3. **The hard-stop meter** for every budget and date limit:
   - ink fill on `--sunken`
   - a heavy ink tick at the wall
   - the number in mono next to what it compares to ("$412 / $500")

   Apply it to the project money and wall rows, Settings → Spend, and roadmap date slips. Reuse the existing meter component if there is one; don't add a second one.
4. **Type roles.**
   - Page titles (h1) and card titles that name a result use Brygada 600.
   - Everything else uses Public Sans.
   - All money, counts, times and IDs use mono with `tabular-nums`.
   - Requirement keys (R-01…) never wrap.
5. **Icons.** Keep the product's existing icon set and stroke. Where a concept has a brand icon in `docs/brand/icons/` (verified, budget-wall, honest-date, veto, record, refusal, checked-by), you may use it, at the existing stroke weight.
6. **Motion.**
   - Interface transitions: 120 ms on the existing `--ease`.
   - Refusals from the server appear **instantly**: no shake, no bounce.
   - Respect `prefers-reduced-motion`.

### PR 3: proof
- Run the full suite once: `pytest -q -n auto`, then `npm run build`, then `npx playwright test` from `web/`.
- Seed the demo with `python tests/e2e/demo.py /tmp/yaaf-demo --serve=8765`, sign in as `dominic@example.com` / `demo-password-123`, and capture **before and after, light and dark** for: Home, Inbox, Team, Chat, Results, Details (roadmap, spend), Settings, and Login.
- Regenerate `docs/screenshots/` from the new build.
- PR description: every deviation from this brief and why; any contrast pair you had to adjust (with measured ratios); and any copy or flow changes you'd *recommend* but didn't make.

## Done means
- No hex values outside `styles.css` token blocks, except where the stylesheet already allowed them.
- Seal appears **only** on checker lines and the logo. Grep `--accent-2` and justify every use.
- Both themes pass the 4.5:1 floor. `yaaf theme sync` (`yaaf/core/desktop_theme.py`) still works, because its `TOKENS` names didn't change.
- Screens match or improve on `docs/brand/screens-target/`, with no layout regressions at 1280 and 1440 px wide.
- Playwright and pytest are green, and golden-eval changes are explained.

## Ask before you
- change any user-facing string other than the YAAF casing
- add a dependency other than the two `@fontsource` packages
- touch server code, the API or rule enforcement
- restructure navigation or page hierarchy
