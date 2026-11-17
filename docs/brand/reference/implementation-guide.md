# Implementation guide

A rollout order for the maintainer. **Nothing has been changed in the live repository or the website.** Every step below is yours to approve.

## 1. The app (one PR, about an hour)
Follow `08-design-system/integration-examples/README.md`:
1. Add `brand-theme.css` and import it after `styles.css`.
2. Swap the font imports (`main.tsx.fonts.diff`), then `npm i @fontsource/public-sans @fontsource/brygada-1918`.
3. Add `Wordmark.tsx` and use it in `shell.tsx`, `pages/Login.tsx` and `pages/Setup.tsx`.
4. Replace `web/public/favicon.svg` and `<meta name="theme-color">`.
5. String fix: "verified by YAAF" → "verified by yaaf".
6. Run `npm run build` and the Playwright suite.

This exact change set was applied to a scratch copy and built cleanly. The result is in `10-mockups/03-product-ui-*`.

## 2. Desktop and tray
- Replace `ICONS` in `yaaf/cli/indicator.py` with `integration-examples/yaaf-tray/indicator_icons.py`.
- Point `yaaf/cli/desktop.py` at the new hicolor SVG.

## 3. Repository
- `pyproject.toml` description: "Run a team of AI agents like a company. The rules live in the server, not the prompt."
- README header: `06-social-media/github/readme-banner-{light,dark}.png` in a `<picture>` element with `prefers-color-scheme`.
- GitHub: Settings → Social preview → `04-web-assets/social-previews/github-social-preview.png`.

## 4. Website (Astro)
- `integration-examples/website/BaseHead.astro`, the favicon set and the manifest.
- Rebuild the hero from `10-mockups/01-homepage-concept-light.html`: headline "Nothing counts until it's checked." and the four rules immediately below it.
- Retake the six product screenshots after step 1.

## 5. Language (optional, the maintainer's call)
- The product says *chairman*. Public copy uses *chair*. Aligning the product is a sweeping string change, and it touches the CEO system prompts in `roles/`, so treat it as its own PR.
- Agent names stay; marketing leads with roles.

## 6. Before any paid promotion or registration
- Formal trademark clearance for YAAF (word) and the aa mark in classes 9 and 42. See `../11-quality-audit/evaluation.md`.
- Don't use "countersign" or "countersigned" in public copy.
