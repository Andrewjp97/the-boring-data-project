/**
 * Measure Cumulative Layout Shift on a URL with headless Chromium
 * (Phase 4 acceptance: AdSlot/AffiliateBlock render with CLS 0, SPEC §11).
 *
 * All non-local requests are aborted, so ad slots never fill — the worst
 * case for reserved layout space: if the fixed-height reservation is wrong,
 * the empty slot collapses and shifts content, and CLS goes above 0.
 *
 * Usage: node scripts/measure-cls.mjs <url> [<url> ...]
 * Prints one JSON line per URL: {"url": ..., "cls": 0}
 *
 * Uses playwright-core from site/node_modules and a system Chromium; set
 * CHROMIUM_PATH if the default (/opt/pw-browsers/chromium or Playwright's
 * own install) is not present.
 */
import { existsSync } from 'node:fs';
import { createRequire } from 'node:module';

const require = createRequire(new URL('../site/package.json', import.meta.url));
const { chromium } = require('playwright-core');

const urls = process.argv.slice(2);
if (urls.length === 0) {
  console.error('usage: node scripts/measure-cls.mjs <url> [<url> ...]');
  process.exit(2);
}

const defaultChromium = '/opt/pw-browsers/chromium';
const executablePath =
  process.env.CHROMIUM_PATH || (existsSync(defaultChromium) ? defaultChromium : undefined);

const browser = await chromium.launch({ executablePath, args: ['--no-sandbox'] });
try {
  // Mobile viewport: the stricter case for responsive ad slots.
  const context = await browser.newContext({
    viewport: { width: 375, height: 667 },
    deviceScaleFactor: 2,
    isMobile: true,
    hasTouch: true,
  });
  await context.route('**/*', (route) => {
    const { hostname } = new URL(route.request().url());
    return hostname === '127.0.0.1' || hostname === 'localhost'
      ? route.continue()
      : route.abort();
  });

  for (const url of urls) {
    const page = await context.newPage();
    await page.addInitScript(() => {
      window.__cls = 0;
      new PerformanceObserver((list) => {
        for (const entry of list.getEntries()) {
          if (!entry.hadRecentInput) window.__cls += entry.value;
        }
      }).observe({ type: 'layout-shift', buffered: true });
    });
    await page.goto(url, { waitUntil: 'load', timeout: 30_000 });
    await page.waitForTimeout(1500);
    // Scroll through the page so below-the-fold slots enter the viewport.
    await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
    await page.waitForTimeout(1000);
    const cls = await page.evaluate(() => window.__cls);
    console.log(JSON.stringify({ url, cls }));
    await page.close();
  }
} finally {
  await browser.close();
}
