#!/usr/bin/env node
/*
 * watch-later.cjs
 * ---------------
 * List (and optionally remove) videos in the signed-in user's YouTube
 * "Watch Later" playlist by driving the already-open, logged-in Opera browser
 * over the Chrome DevTools Protocol (CDP) on port 9222.
 *
 * PREREQUISITE: Opera must be running with remote debugging enabled:
 *   opera.exe --remote-debugging-port=9222 --remote-allow-origins=*
 * (Allan has this auto-launched via OperaCDP.bat in the Startup folder.)
 *
 * Usage:
 *   node watch-later.cjs list              -> prints JSON [{index,title,id,url}]
 *   node watch-later.cjs list --json out.json
 *   node watch-later.cjs remove-all        -> removes EVERY video (asks nothing)
 *   node watch-later.cjs remove-first N    -> removes the first N videos
 *
 * NOTE: removal uses the 3-dot menu -> "Remove from Watch later" UI flow.
 * YouTube's DOM changes often; if selectors break, re-inspect the menu.
 */
const { chromium } = require('playwright');

const CDP = process.env.CDP_ENDPOINT || 'http://localhost:9222';
const cmd = process.argv[2] || 'list';

async function getPage(context) {
  const page = await context.newPage();
  await page.goto('https://www.youtube.com/playlist?list=WL', {
    waitUntil: 'domcontentloaded',
    timeout: 60000,
  });
  // Let the playlist render; slow connection tolerant.
  await page.waitForTimeout(4000);
  // Scroll to load all items (lazy loaded).
  let prev = 0;
  for (let i = 0; i < 30; i++) {
    await page.mouse.wheel(0, 4000);
    await page.waitForTimeout(1200);
    const count = await page.locator('ytd-playlist-video-renderer').count();
    if (count === prev) break;
    prev = count;
  }
  return page;
}

async function listVideos(page) {
  const rows = page.locator('ytd-playlist-video-renderer');
  const n = await rows.count();
  const out = [];
  for (let i = 0; i < n; i++) {
    const row = rows.nth(i);
    const title = (await row.locator('#video-title').first().textContent().catch(() => '') || '').trim();
    const href = await row.locator('#video-title').first().getAttribute('href').catch(() => '');
    const m = href && href.match(/[?&]v=([A-Za-z0-9_-]{11})/);
    const id = m ? m[1] : null;
    if (id) out.push({ index: i + 1, title, id, url: `https://youtu.be/${id}` });
  }
  return out;
}

(async () => {
  const browser = await chromium.connectOverCDP(CDP);
  const context = browser.contexts()[0];
  const page = await getPage(context);

  if (cmd === 'list') {
    const vids = await listVideos(page);
    const jsonFlagIdx = process.argv.indexOf('--json');
    const json = JSON.stringify(vids, null, 2);
    if (jsonFlagIdx !== -1 && process.argv[jsonFlagIdx + 1]) {
      require('fs').writeFileSync(process.argv[jsonFlagIdx + 1], json, 'utf8');
      console.error(`[saved] ${process.argv[jsonFlagIdx + 1]} (${vids.length} videos)`);
    }
    console.log(json);
    await page.close();
    await browser.close();
    return;
  }

  if (cmd === 'remove-all' || cmd === 'remove-first') {
    const limit = cmd === 'remove-first' ? parseInt(process.argv[3] || '0', 10) : Infinity;
    let removed = 0;
    // Always remove the TOP item repeatedly; the list re-indexes after each removal.
    while (removed < limit) {
      const rows = page.locator('ytd-playlist-video-renderer');
      if ((await rows.count()) === 0) break;
      const first = rows.first();
      const title = (await first.locator('#video-title').first().textContent().catch(() => '') || '').trim();
      // Open the 3-dot action menu on that row.
      await first.locator('ytd-menu-renderer button, #button[aria-label]').first().click().catch(() => {});
      await page.waitForTimeout(1000);
      // Click "Remove from Watch later" in the popup menu.
      const item = page.locator('tp-yt-paper-item, ytd-menu-service-item-renderer', {
        hasText: /Remove from Watch later/i,
      }).first();
      const ok = await item.isVisible().catch(() => false);
      if (!ok) {
        console.error('Could not find "Remove from Watch later" menu item. DOM may have changed.');
        break;
      }
      await item.click();
      await page.waitForTimeout(1500);
      removed++;
      console.error(`[removed ${removed}] ${title}`);
    }
    console.log(JSON.stringify({ removed }));
    await page.close();
    await browser.close();
    return;
  }

  console.error(`Unknown command: ${cmd}. Use: list | remove-all | remove-first N`);
  await page.close();
  await browser.close();
  process.exit(2);
})();
