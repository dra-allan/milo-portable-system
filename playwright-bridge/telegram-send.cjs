#!/usr/bin/env node
/*
 * telegram-send.cjs
 * -----------------
 * Send a message to Telegram via the Bot API (NOT the browser).
 * Handles the UTF-8 encoding gotcha that breaks PowerShell/Invoke-RestMethod
 * ("Bad Request: text must be encoded in UTF-8") by sending raw UTF-8 bytes.
 *
 * Reads the message text from:
 *   - a file path passed as --file <path>, OR
 *   - stdin (piped), OR
 *   - the remaining CLI args joined together.
 *
 * Config (env overrides, sensible defaults for Allan's @Milo_drabot):
 *   TELEGRAM_BOT_TOKEN   default: the Milo bot token
 *   TELEGRAM_CHAT_ID     default: Allan's chat id
 *
 * Usage:
 *   node telegram-send.cjs "hello world"
 *   node telegram-send.cjs --file summary.txt
 *   echo "hi" | node telegram-send.cjs
 *   node telegram-send.cjs --file summary.txt --plain   (no HTML parse mode)
 */
const fs = require('fs');
const https = require('https');

const BOT_TOKEN = process.env.TELEGRAM_BOT_TOKEN || process.env.TELEGRAM_BOT_TOKEN || '';
const CHAT_ID = process.env.TELEGRAM_CHAT_ID || '8101147332';

const args = process.argv.slice(2);
let plain = false;
let filePath = null;
const rest = [];
for (let i = 0; i < args.length; i++) {
  if (args[i] === '--plain') plain = true;
  else if (args[i] === '--file') { filePath = args[++i]; }
  else rest.push(args[i]);
}

function readStdin() {
  return new Promise((resolve) => {
    let data = '';
    if (process.stdin.isTTY) return resolve('');
    process.stdin.setEncoding('utf8');
    process.stdin.on('data', (c) => (data += c));
    process.stdin.on('end', () => resolve(data));
  });
}

async function getText() {
  if (filePath) return fs.readFileSync(filePath, 'utf8');
  if (rest.length) return rest.join(' ');
  return (await readStdin());
}

function send(text) {
  const payload = { chat_id: CHAT_ID, text };
  if (!plain) payload.parse_mode = 'HTML';
  const body = Buffer.from(JSON.stringify(payload), 'utf8');
  const options = {
    hostname: 'api.telegram.org',
    path: `/bot${BOT_TOKEN}/sendMessage`,
    method: 'POST',
    headers: {
      'Content-Type': 'application/json; charset=utf-8',
      'Content-Length': body.length,
    },
  };
  return new Promise((resolve, reject) => {
    const req = https.request(options, (res) => {
      let out = '';
      res.on('data', (d) => (out += d));
      res.on('end', () => {
        try {
          const json = JSON.parse(out);
          if (json.ok) resolve(json);
          else reject(new Error(out));
        } catch { reject(new Error(out)); }
      });
    });
    req.on('error', reject);
    req.write(body);
    req.end();
  });
}

(async () => {
  const text = (await getText() || '').trim();
  if (!text) {
    console.error('No message text provided (use --file, stdin, or args).');
    process.exit(2);
  }
  // Telegram caps messages at 4096 chars; split on double-newlines if needed.
  const chunks = [];
  if (text.length <= 4096) {
    chunks.push(text);
  } else {
    let buf = '';
    for (const para of text.split('\n\n')) {
      if ((buf + '\n\n' + para).length > 4000) {
        if (buf) chunks.push(buf);
        buf = para;
      } else {
        buf = buf ? buf + '\n\n' + para : para;
      }
    }
    if (buf) chunks.push(buf);
  }
  try {
    for (const c of chunks) await send(c);
    console.error(`[sent] ${chunks.length} message(s) to chat ${CHAT_ID}`);
  } catch (e) {
    console.error('Telegram send failed:', e.message);
    process.exit(1);
  }
})();
