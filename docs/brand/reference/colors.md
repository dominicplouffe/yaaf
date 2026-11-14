# Colour

## The idea
Registrar's ink on registry paper, and one seal. **Ink is the maker. Seal is the countersign.** Navy only ever appears as ink on paper. The dark theme sits on neutral Carbon, deliberately outside the category's navy-and-violet convention.

## Brand palette

| Name | HEX | RGB | HSL | CSS variable | Use |
|---|---|---|---|---|---|
| **registrar-ink** | `#161A2E` | 22, 26, 46 | 230°, 35%, 13% | `--yaaf-brand-registrar-ink` | Primary ink. Text, marks, the maker. Navy only ever appears as ink on paper. |
| **registry** | `#F4F4F1` | 244, 244, 241 | 60°, 12%, 95% | `--yaaf-brand-registry` | Primary paper. Default light background. Near-neutral on purpose (not ivory). |
| **seal** | `#B8203A` | 184, 32, 58 | 350°, 70%, 42% | `--yaaf-brand-seal` | The countersign: the checker's signature. The italic a, and the name of whoever checked. Never a status, never an error. |
| **carbon** | `#121316` | 18, 19, 22 | 225°, 10%, 8% | `--yaaf-brand-carbon` | Dark ground. Neutral, never navy. |
| **seal-on-dark** | `#EE4259` | 238, 66, 89 | 352°, 83%, 60% | `--yaaf-brand-seal-on-dark` | The same red, lifted for dark grounds (hue 352°, 4.9:1 on Carbon). |
| **registrar-blue** | `#2C46A6` | 44, 70, 166 | 227°, 58%, 41% | `--yaaf-brand-registrar-blue` | Interactive: links, focus, primary actions in product UI. |
| **rule-blue** | `#9FB0D0` | 159, 176, 208 | 219°, 34%, 72% | `--yaaf-brand-rule-blue` | Ledger rule. Hairlines and dividers only. Never text (2:1). |
| **white** | `#FFFFFF` | 255, 255, 255 | 0°, 0%, 100% | `--yaaf-brand-white` | Raised surfaces on light. |

## Proportion
Roughly **70% Registry / Carbon, 25% Ink / Paper-ink, under 5% Seal.** Seal is scarce on purpose: if everything is countersigned, nothing is. There is **one red**: Seal on light (#B8203A) and the same hue lifted for dark (#EE4259). Never put both side by side.

## Rules
1. **Seal is the checker's signature**: the italic *a*, and the name of whoever checked a result ("checked by *Compliance*"). It is **never a status**: "verified" stays green (`good`), because to developers red means failure. It is **never an error colour**: errors use `critical` (a red-orange at hue about 15°) and always come with an icon or a word. It is **never decoration**: emphasis in headlines is an ink italic, not a red one.
2. Rule Blue is for lines only. It fails text contrast by design.
3. Don't tint the paper warmer. The near-neutral Registry is what keeps yaaf clear of Anthropic's ivory.
4. In product UI the interactive colour is **Registrar Blue**, not Seal. Seal is for brand and verification states.

## Neutral scale (paper → ink)
`0` #FFFFFF · `50` #F4F4F1 · `100` #EAEAE5 · `200` #DADAD4 · `300` #BFC0BB · `400` #93959C · `500` #6E717C · `600` #535766 · `700` #3A3E4E · `800` #262A3B · `900` #161A2E · `950` #0E1120

## Carbon scale (dark surfaces)
`900` #121316 · `850` #17181B · `800` #1B1C20 · `750` #222328 · `700` #2C2D33 · `600` #3A3B42

## Theme mappings
See `../08-design-system/tokens.css`, `theme-light.json` and `theme-dark.json`.

| Token | Light | Dark |
|---|---|---|
| `page` | `#F4F4F1` | `#121316` |
| `surface` | `#FFFFFF` | `#1B1C20` |
| `surface-2` | `#F9F9F7` | `#222328` |
| `sunken` | `#EAEAE5` | `#0E0F11` |
| `ink` | `#161A2E` | `#ECECE7` |
| `ink-2` | `#4A4F60` | `#B4B6BC` |
| `muted` | `#5E6270` | `#9A9DA6` |
| `border` | `#DADAD4` | `#2C2D33` |
| `border-strong` | `#BFC0BB` | `#3A3B42` |
| `rule` | `#9FB0D0` | `#4A5570` |
| `grid` | `#E4E4DF` | `#26272C` |
| `accent` | `#2C46A6` | `#8FA4F0` |
| `on-accent` | `#FFFFFF` | `#121316` |
| `accent-hover` | `#23398A` | `#A9B9F4` |
| `seal` | `#B8203A` | `#EE4259` |
| `on-seal` | `#FFFFFF` | `#121316` |
| `good` | `#1D6B45` | `#4FC48C` |
| `warning` | `#8A5B00` | `#E0AC36` |
| `serious` | `#9C4A12` | `#F09A55` |
| `critical` | `#B3401A` | `#F0835F` |
| `on-critical` | `#FFFFFF` | `#121316` |
| `focus` | `#2C46A6` | `#8FA4F0` |
| `selection` | `#DCE3F7` | `#2A3352` |
| `c1` | `#2C46A6` | `#637FEE` |
| `c2` | `#B8203A` | `#EE4259` |
| `c3` | `#00818F` | `#3DB8C4` |
| `c4` | `#9A6B00` | `#D9A53A` |
| `c5` | `#AE6ECF` | `#D78FEF` |

## Interactive states
| State | Light | Dark |
|---|---|---|
| Link / primary action | `#2C46A6` | `#8FA4F0` |
| Hover | `#23398A` | `#A9B9F4` |
| Focus ring | 2 px `#2C46A6` + 2 px offset | 2 px `#8FA4F0` + 2 px offset |
| Selection | `#DCE3F7` | `#2A3352` |
| Disabled | ink at 40% opacity, no colour change | same |

## Contrast (measured, WCAG 2.x)

| Theme | Token | on page | on surface | Result |
|---|---|---|---|---|
| Light | ink `#161A2E` | 15.60 | 17.19 | AA ✓ |
| Light | ink-2 `#4A4F60` | 7.39 | 8.14 | AA ✓ |
| Light | muted `#5E6270` | 5.51 | 6.07 | AA ✓ |
| Light | accent `#2C46A6` | 7.52 | 8.29 | AA ✓ |
| Light | seal `#B8203A` | 5.76 | 6.35 | AA ✓ |
| Light | good `#1D6B45` | 5.88 | 6.48 | AA ✓ |
| Light | warning `#8A5B00` | 5.33 | 5.87 | AA ✓ |
| Light | serious `#9C4A12` | 5.60 | 6.17 | AA ✓ |
| Light | critical `#B3401A` | 5.20 | 5.73 | AA ✓ |
| Dark | ink `#ECECE7` | 15.67 | 14.36 | AA ✓ |
| Dark | ink-2 `#B4B6BC` | 9.16 | 8.40 | AA ✓ |
| Dark | muted `#9A9DA6` | 6.85 | 6.28 | AA ✓ |
| Dark | accent `#8FA4F0` | 7.73 | 7.08 | AA ✓ |
| Dark | seal `#EE4259` | 4.92 | 4.51 | AA ✓ |
| Dark | good `#4FC48C` | 8.50 | 7.79 | AA ✓ |
| Dark | warning `#E0AC36` | 8.96 | 8.21 | AA ✓ |
| Dark | serious `#F09A55` | 8.37 | 7.67 | AA ✓ |
| Dark | critical `#F0835F` | 7.18 | 6.58 | AA ✓ |

## Chart slots
`c1…c5` are a fixed order assigned once and never cycled. The minimum pairwise ΔE (CIELAB) across normal vision and simulated protanopia, deuteranopia and tritanopia is **20.9** in light and **16.4** in dark. Contrast on surface: light [8.29, 6.35, 4.63, 4.69, 3.54], dark [4.7, 4.51, 7.18, 7.61, 7.35]. Chart marks need at least 3:1 (WCAG 1.4.11), and all pass.
