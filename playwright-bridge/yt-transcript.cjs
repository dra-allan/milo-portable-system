const { chromium } = require('playwright');
(async () => {
  const browser = await chromium.connectOverCDP('http://localhost:9222');
  const context = browser.contexts()[0];
  const page = await context.newPage();
  await page.goto('https://youtubetotranscript.com/?v=zZ1bbUU0bR8', {
    waitUntil: 'networkidle',
    timeout: 30000
  });
  await page.waitForTimeout(2000);

  // Get the output div text
  const output = await page.locator('#output, .transcript-output, [class*="transcript"]').first();
  const visible = await output.isVisible().catch(() => false);
  if (visible) {
    const text = await output.textContent();
    console.log(text);
  } else {
    // Try getting all visible text
    const body = await page.locator('body').textContent();
    console.log(body.substring(0, 5000));
  }

  await page.close();
  await browser.close();
})();
