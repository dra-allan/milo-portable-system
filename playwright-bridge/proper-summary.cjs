const { chromium } = require('playwright');
const https = require('https');
const fs = require('fs');

const BOT_TOKEN = process.env.TELEGRAM_BOT_TOKEN || '';
const CHAT_ID = process.env.TELEGRAM_CHAT_ID || '8101147332';

if (!BOT_TOKEN) {
  console.error('TELEGRAM_BOT_TOKEN is not set. Set it before running this script.');
  process.exit(1);
}

const videos = [
  { id: 'DJP5hjPPT1E', title: 'How To Produce a Riddim!' },
  { id: 'id1rzzJsQ98', title: 'How Much I Make Owning a 5th Division Kenyan Club' },
  { id: 'oWRI6xKEZMk', title: 'How Hackers Hack Websites' },
  { id: '7W89eklcj3c', title: 'How to Make a Cheap Camera Cinematic' },
  { id: 'CDeB98AjJY0', title: '30 Days of No Nut November' },
  { id: 'kZNnz8ifd7c', title: 'UPDF etimpudde Al Shabab' },
  { id: 'vSG0m6pNIQE', title: 'Omnisphere 3 Is Finally Here' },
  { id: '1EYUhpimyxc', title: 'Why can parrots talk?' },
  { id: 'eFMedQGPVNk', title: 'Stop Using Reverb Like This' },
  { id: 'iQ7r8d3k_lY', title: 'The Dark Truth About Lucky Patcher' },
  { id: 'ngjYe5KTVqM', title: 'HOW WE GOT Monetized in 48 HOURS' },
  { id: 'H01eQ_xQ6co', title: 'How to make Modern Zouk Beat' },
  { id: '1r1lkd6WTQw', title: 'The Secret Mix Buss Technique' },
  { id: 'MSS41hSN7ZM', title: 'I Made $296,792 with 1 Faceless YouTube Channel' },
  { id: 'Kjsku8HR73s', title: '9 Logic Pro Tips That Feel Like Cheating' },
  { id: 'E8LITZg-TXo', title: '24 Small Ableton Live 12 changes' },
  { id: 'TpEkCPyi69c', title: 'An Ableton Productivity Hack' },
  { id: 'RTrMYZzD_CM', title: '9 Failed Superhero Franchises' },
  { id: '7rWYmBw06H0', title: 'Buli producer wetaaga plugins zino' },
  { id: 'Y9JF_yYTNlw', title: 'The BEST Smartphones of 2025' }
];

function sendTelegram(text) {
  return new Promise((resolve) => {
    const payload = JSON.stringify({ chat_id: CHAT_ID, text: text, parse_mode: 'HTML' });
    const req = https.request({
      hostname: 'api.telegram.org',
      path: '/bot' + BOT_TOKEN + '/sendMessage',
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(payload) }
    }, (res) => {
      let d = '';
      res.on('data', c => d += c);
      res.on('end', () => resolve(JSON.parse(d)));
    });
    req.on('error', resolve);
    req.write(payload);
    req.end();
  });
}

(async () => {
  const browser = await chromium.connectOverCDP('http://localhost:9222');

  for (let i = 0; i < videos.length; i++) {
    const v = videos[i];
    console.log(`\n[${i + 1}/${videos.length}] ${v.title}`);
    const page = await browser.contexts()[0].newPage();

    try {
      await page.goto('https://www.youtube.com/watch?v=' + v.id, { waitUntil: 'domcontentloaded', timeout: 20000 });
      // Wait longer for page to settle
      await page.waitForTimeout(5000);

      // Extract full page content via page API
      const data = await page.evaluate(() => {
        const result = { title: '', channel: '', views: '', desc: '', aiSummary: '', chapters: [] };

        // Title
        const titleEl = document.querySelector('h1 yt-formatted-string');
        if (titleEl) result.title = titleEl.textContent.trim();

        // Channel
        const chanEl = document.querySelector('#owner #channel-name yt-formatted-string a');
        if (chanEl) result.channel = chanEl.textContent.trim();

        // Description text (full, from the expandable section)
        const descEl = document.querySelector('#description-inner');
        if (descEl) {
          result.desc = descEl.textContent.trim();
          // Remove common boilerplate
          result.desc = result.desc.replace(/\.\.\.more/g, '').trim();
        }

        // AI Summary - look for the summary section
        const summaryEl = document.querySelector('ytd-watch-metadata yt-formatted-string#description, yt-formatted-string[slot="content"]');
        if (summaryEl) result.aiSummary = summaryEl.textContent.trim();

        // Also look for structured summary in the metadata
        const metadataEls = document.querySelectorAll('ytd-video-description-section-renderer, ytd-watch-description-text');
        metadataEls.forEach(el => {
          const text = el.textContent.trim();
          if (text.length > 50 && text.length < 2000) {
            result.aiSummary = result.aiSummary || text;
          }
        });

        // Chapters
        const chapterEls = document.querySelectorAll('ytd-chapter-renderer');
        chapterEls.forEach(el => {
          const time = el.querySelector('#time')?.textContent?.trim() || '';
          const title = el.querySelector('#title')?.textContent?.trim() || '';
          if (time && title) result.chapters.push(time + ' - ' + title);
        });

        // View count
        const viewEl = document.querySelector('#count yt-formatted-string');
        if (viewEl) result.views = viewEl.textContent.trim();

        return result;
      });

      // Build a proper summary
      let msg = '<b>' + (data.title || v.title).substring(0, 100).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;') + '</b>\n';
      if (data.channel) msg += 'Channel: ' + data.channel.replace(/&/g, '&amp;') + '\n';
      if (data.views) msg += 'Views: ' + data.views + '\n';
      msg += 'https://youtu.be/' + v.id + '\n\n';

      // Add AI summary as the main summary
      if (data.aiSummary && data.aiSummary.length > 50) {
        msg += '<b>AI Summary:</b>\n' + data.aiSummary.substring(0, 1000).replace(/&/g, '&amp;') + '\n\n';
      }

      // Add description (truncated)
      if (data.desc && data.desc.length > 50) {
        const descText = data.desc.substring(0, 2500);
        msg += '<b>Description:</b>\n' + descText.replace(/&/g, '&amp;').substring(0, 1500) + '\n\n';
      }

      // Add chapters
      if (data.chapters && data.chapters.length > 0) {
        msg += '<b>Chapters:</b>\n' + data.chapters.slice(0, 10).join('\n').replace(/&/g, '&amp;') + '\n\n';
      }

      // Send to Telegram
      if (msg.length > 4000) msg = msg.substring(0, 3900) + '...';
      const r = await sendTelegram(msg);
      console.log('Sent to Telegram: ' + (r.ok ? 'OK' : 'FAIL'));

    } catch(e) {
      console.log('ERROR: ' + e.message.substring(0, 50));
      const fallback = '<b>' + v.title.substring(0, 100).replace(/&/g, '&amp;') + '</b>\nhttps://youtu.be/' + v.id + '\n\n(Could not fetch details)';
      await sendTelegram(fallback);
    }

    await page.close();
    // Small delay between videos
    await new Promise(r => setTimeout(r, 1000));
  }

  console.log('\n\nAll summaries sent to Telegram!');
  await browser.close();
})();
