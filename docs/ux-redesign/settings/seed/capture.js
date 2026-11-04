// Capture every Settings section of the demo install, full page, with counts of
// words, controls and "Why?" folds. Usage: node capture.js <outdir> [width] [light|dark]
const { chromium } = require('playwright');
const fs = require('fs');
const [out, width = '1440', scheme = 'light'] = process.argv.slice(2);
const BASE = 'http://localhost:8765/app/#';
const SECTIONS = ['providers', 'models', 'prices', 'search', 'founder', 'slack', 'people', 'account'];
(async () => {
  fs.mkdirSync(out, { recursive: true });
  const b = await chromium.launch();
  const ctx = await b.newContext({ viewport: { width: +width, height: 900 }, deviceScaleFactor: +(process.env.DSF || 1), colorScheme: scheme });
  const p = await ctx.newPage();
  await p.goto(BASE + '/login');
  await p.fill('input[type=email]', 'dominic@example.com');
  await p.fill('input[type=password]', 'demo-password-123');
  await p.click('button[type=submit]');
  await p.waitForTimeout(1500);
  const metrics = {};
  for (const s of SECTIONS) {
    await p.goto(BASE + '/settings?section=' + s);
    await p.waitForLoadState('networkidle');
    await p.waitForTimeout(1200);
    await p.screenshot({ path: `${out}/${s}.png`, fullPage: true });
    // above-the-fold too
    await p.screenshot({ path: `${out}/${s}-fold.png`, fullPage: false });
    metrics[s] = await p.evaluate(() => {
      const main = document.querySelector('.settings-lay > div:last-child') || document.body;
      const words = (el) => (el.innerText.match(/\S+/g) || []).length;
      const visibleText = words(main);
      // text including closed <details>
      const allText = (main.textContent.match(/\S+/g) || []).length;
      const q = (sel) => main.querySelectorAll(sel).length;
      return {
        pageHeight: document.documentElement.scrollHeight,
        visibleWords: visibleText,
        allWords: allText,
        buttons: q('button'),
        inputs: q('input:not([type=hidden]), select, textarea'),
        whyFolds: q('details.s-why'),
        details: q('details'),
        helpLines: q('.help'),
        badges: q('.badge, .chip'),
        cards: q('.card'),
        headings: [...main.querySelectorAll('h2,h3,h4')].map(h => h.innerText.trim()),
        buttonLabels: [...main.querySelectorAll('button')].map(x => x.innerText.trim()).filter(Boolean),
      };
    });
    // rail text
    metrics[s].rail = await p.evaluate(() => [...document.querySelectorAll('.srail button')].map(b => b.innerText.replace(/\n/g, ' | ')));
    console.log(s, metrics[s].pageHeight, metrics[s].visibleWords, metrics[s].allWords);
  }
  fs.writeFileSync(`${out}/metrics.json`, JSON.stringify(metrics, null, 2));
  await b.close();
})();
