const { chromium } = require('playwright');
(async () => {
  const [src, out, ...ids] = process.argv.slice(2);
  const b = await chromium.launch();
  const p = await (await b.newContext({ viewport:{width:2000,height:1000}, deviceScaleFactor:+(process.env.DSF||1.5) })).newPage();
  await p.goto('file://'+src, {waitUntil:'networkidle'}); await p.waitForTimeout(800);
  for (const id of ids) { const el = await p.$('#'+id); await el.screenshot({path:`${out}/${id}.png`}); console.log(id); }
  await b.close();
})();
