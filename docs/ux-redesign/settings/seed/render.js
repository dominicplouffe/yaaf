// Render each frame of mockups.html to after/<id>.png.
//   node docs/ux-redesign/settings/seed/render.js
// Uses the Playwright that ships with the dev environment; DSF=1 for a quick look.
const path = require('path');
const { chromium } = require('playwright');
const here = path.resolve(__dirname, '..');
const FRAMES = ['overview', 'providers', 'provider-open', 'models', 'prices', 'founder', 'slack', 'people', 'phone', 'system', 'overview-dark'];
(async () => {
  const b = await chromium.launch();
  const p = await (await b.newContext({ viewport: { width: 1560, height: 1000 }, deviceScaleFactor: +(process.env.DSF || 1.5) })).newPage();
  await p.goto('file://' + path.join(here, 'mockups.html'), { waitUntil: 'networkidle' });
  await p.evaluate(() => document.fonts.ready);
  await p.waitForTimeout(500);
  for (const id of (process.argv.slice(2).length ? process.argv.slice(2) : FRAMES)) {
    const el = await p.$('#' + id);
    await el.screenshot({ path: path.join(here, 'after', `${id}.png`) });
    console.log(id);
  }
  await b.close();
})();
