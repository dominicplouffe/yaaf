# Typography

## The system

| Role | Typeface | Why |
|---|---|---|
| **Display, headlines, the voice of the record** | **Brygada 1918** (OFL) | A digital revival of a typeface cut in 1918 for Poland's new state institutions. It is literally a registry type. It has sharp, slightly calligraphic italics, which is where the *signature* a comes from. It is rarely used, so it is not this year's editorial serif. |
| **Interface and running text** | **Public Sans** (OFL) | Built for the US Web Design System: neutral, highly legible and civic. It does the work so Brygada can do the talking. |
| **Receipts, numbers, code, CLI** | **IBM Plex Mono** (OFL) | Engineered, readable at 12 px, clear figures. Every dollar, run count and ID is set in it. |

The pairing is chosen to avoid two traps. One is the fashionable SaaS grotesk (Inter, Geist). The other is the serif-plus-humanist-sans formula that would read as Anthropic-adjacent. Brygada's 1918 institutional cut and Public Sans' civic neutrality put yaaf in the register of *public record*, not *editorial brand*.

## Scale

**Marketing and documents** (1.25 ratio, 16 px base):

| Token | px | Typeface | Use |
|---|---:|---|---|
| hero | 96 | Brygada 700 | One per page, homepage only |
| display | 76 | Brygada 600 | Section openers, covers |
| h1 | 60 | Brygada 600 | Page titles |
| h2 | 48 | Brygada 600 | |
| h3 | 38 | Brygada 600 | |
| h4 | 30 | Brygada 500 | |
| h5 | 24 | Public Sans 600 | Card titles |
| h6 | 20 | Public Sans 600 | |
| lead | 18 | Public Sans 400 | Intro paragraphs |
| body | 16 | Public Sans 400 | Running text, 1.55 line height |
| small | 14 | Public Sans 400 / Plex Mono 400 | Captions, receipts |
| micro | 12 | Plex Mono 500, +0.08em, uppercase | Labels. **12 px is the floor.** |

**Product UI** keeps the app's existing scale (12 / 13 / 15 / 16 / 17 / 20 / 28 / 34 / 30-stat), already tuned for density. Only the families change: page titles move to Brygada, everything else to Public Sans.

## Rules
1. **Italic means signed.** Use Brygada italic for the checker's name, a countersigned verdict or a pull quote, never for decoration.
2. **Headlines are short and in sentence case.** No title case, no all-caps headlines.
3. **Numbers go in mono** whenever they are money, counts, times or IDs: `$2.31 · 1 run`.
4. **Line length** 60–75 characters for reading, 45 for UI help text.
5. **Tracking:** display sizes −1% to −2%; micro labels +8%.

## Language support

| Typeface | Scripts |
|---|---|
| Brygada 1918 | Latin, Latin Extended, Cyrillic, Greek, Vietnamese |
| Public Sans | Latin, Latin Extended, Vietnamese (**no Cyrillic or Greek**: fall back to IBM Plex Sans or system-ui) |
| IBM Plex Mono | Latin, Latin Extended, Cyrillic |

## Licensing
All three are under the SIL Open Font License 1.1. They are free for commercial use, embedding and self-hosting, and can be redistributed with the licence. Web font files (woff2: latin and latin-ext subsets) and each `OFL.txt` are in `fonts/`. Desktop TTFs and the full set of subsets are available from Google Fonts or via the `@fontsource/brygada-1918`, `@fontsource/public-sans` and `@fontsource/ibm-plex-mono` npm packages.

## The wordmark is not typed
The logo is drawn from Brygada 1918 Bold, with custom spacing (y→a 0, a→*a* +4, *a*→f −28 per 1000 units, so the three gaps read as equal) and outlined to paths. Never retype "yaaf" in the font in place of the logo artwork.

## CSS

```css
@import "@fontsource/brygada-1918/600.css";
@import "@fontsource/brygada-1918/600-italic.css";
@import "@fontsource/public-sans/400.css";
@import "@fontsource/public-sans/600.css";
@import "@fontsource/ibm-plex-mono/400.css";

h1, h2, h3, .display { font-family: var(--yaaf-font-display); font-weight: 600; letter-spacing: -0.015em; }
body { font-family: var(--yaaf-font-text); font-size: var(--yaaf-text-body); line-height: 1.55; }
.receipt, code, .num { font-family: var(--yaaf-font-mono); font-variant-numeric: tabular-nums; }
.signed { font-family: var(--yaaf-font-display); font-style: italic; color: var(--yaaf-seal); }
```
