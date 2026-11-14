# Integration examples

These are written against yaaf's **actual** stack as found in the repository (v0.6.0):
- `web/` is React 18 + Vite. Styling is one plain-CSS design system (`web/src/styles.css`) with semantic custom properties. Fonts come from `@fontsource`.
- The logo is text in three places: `y<span>aa</span>f` in shell.tsx:395, Login.tsx:52 and Setup.tsx:108. Interestingly, it already sets the "aa" apart. The new identity sharpens that instinct to the second *a* only: the countersign.
- `yaaf/cli/indicator.py` draws three tray faces as inline SVG, and `web/public/favicon.svg` is a blue gradient tile.
- The marketing site is Astro.

**Nothing in the production repository was changed.** Applying these is your call. They are a branch's worth of work, not a deploy.

## yaaf-web/
| File | What to do |
|---|---|
| `brand-theme.css` | Copy to `web/src/brand-theme.css`. Import it after `styles.css`. It redefines the stylesheet's own variable names (`--page`, `--ink`, `--accent`, `--c1…c5`…), so every component restyles with **zero component edits**. `--accent-2` becomes the Seal. |
| `main.tsx.fonts.diff` | Swap the font imports: `npm i @fontsource/public-sans @fontsource/brygada-1918 @fontsource/ibm-plex-mono`. |
| `Wordmark.tsx` | Replace the three text logos with the SVG wordmark: `shell.tsx` (sidebar, `height=24`), and `pages/Login.tsx` and `pages/Setup.tsx` (`<h1 aria-label="yaaf"><Wordmark height=44 /></h1>`). |
| favicon | Replace `web/public/favicon.svg` with `04-web-assets/favicons/favicon.svg`. It flips for dark browser UI. Set `<meta name="theme-color">` per scheme (see `04-web-assets/head-snippet.html`). |

The theme also makes primary buttons ink (links and selection stay Registrar Blue), closes the Results evidence trail on the double rule, signs the check line in Seal italic (the one place the product shows the countersign), keeps requirement keys on one line, and leaves the product's own monospace stack alone. Two existing rules stay true after the swap: **12 px is the floor**, and **status colours always ship with an icon or a word**. Run the existing Playwright suite (`web/playwright.config.ts`) after applying.

**Tested:** the screens in `10-mockups/03-product-ui-*` were captured from a scratch copy of yaaf 0.6.0 with exactly these changes applied (theme file, font swap, Wordmark in three places), running the repo's own `tests/e2e/demo.py` seed. `npm run build` (tsc + vite) passed. One existing layout issue shows up but isn't caused by the theme: requirement keys such as "R-02" wrap in the Results "Serves" column. It happens with the original monospace too.

## yaaf-tray/
`indicator_icons.py` holds the `ICONS` dict for `yaaf/cli/indicator.py`, with the same three faces and the same 64-unit tile. The desktop install (`yaaf/cli/desktop.py`) copies `web/public/favicon.svg` into hicolor. Point it at `05-application-assets/desktop/linux/hicolor/scalable/apps/yaaf.svg` instead (it doesn't need the dark-mode CSS).

## Desktop themes (`yaaf theme sync`)
`yaaf/core/desktop_theme.py` pours an Omarchy palette into the same tokens and keeps the 4.5:1 floor. That stays compatible. The brand theme is simply what you see when no desktop theme is synced.

## website/
- `BaseHead.astro`: favicon, manifest, theme-colour and Open Graph tags.
- `tailwind.preset.cjs`: brand colours and fonts, if the site adopts Tailwind. Otherwise use `../tokens.css` directly.

## Copy changes that come with the brand
- `pyproject.toml` → `description = "Run a team of AI agents like a company. The rules live in the server, not the prompt."`
- UI string "verified by YAAF" → "verified by yaaf" (Results page).
- Public copy: "chair", not "chairman". Product rename is optional and is the maintainer's call.
