#!/usr/bin/env node
/*
 * youtube-transcript.cjs
 * -----------------------
 * Fetch a YouTube transcript by video ID (or URL) using yt-transcript-kit and
 * save it to the transcripts/ folder. Returns the plain text on stdout.
 *
 * This is the RELIABLE method that works on Allan's slow Uganda connection
 * (the youtube timedtext API returns empty, and transcript sites are Cloudflare
 * blocked over raw HTTP). yt-transcript-kit works via npx with no browser.
 *
 * Usage:
 *   node youtube-transcript.cjs <videoIdOrUrl> [langCode]
 *   node youtube-transcript.cjs dQw4w9WgXcQ
 *   node youtube-transcript.cjs "https://youtu.be/dQw4w9WgXcQ" en
 *
 * Exit codes:
 *   0 = success (transcript printed + saved)
 *   1 = no transcript available (e.g. non-English video, no captions)
 *   2 = bad usage
 */
const { execSync } = require('child_process');
const fs = require('fs');
const path = require('path');

function extractId(input) {
  if (!input) return null;
  // Already a bare 11-char id
  if (/^[A-Za-z0-9_-]{11}$/.test(input)) return input;
  const patterns = [
    /[?&]v=([A-Za-z0-9_-]{11})/,      // watch?v=
    /youtu\.be\/([A-Za-z0-9_-]{11})/,  // youtu.be/
    /embed\/([A-Za-z0-9_-]{11})/,      // /embed/
    /shorts\/([A-Za-z0-9_-]{11})/,     // /shorts/
  ];
  for (const re of patterns) {
    const m = input.match(re);
    if (m) return m[1];
  }
  return null;
}

const rawArg = process.argv[2];
const lang = process.argv[3] || 'en';
const id = extractId(rawArg);

if (!id) {
  console.error('Usage: node youtube-transcript.cjs <videoIdOrUrl> [langCode]');
  process.exit(2);
}

const outDir = path.join(__dirname, 'transcripts');
if (!fs.existsSync(outDir)) fs.mkdirSync(outDir, { recursive: true });
const outFile = path.join(outDir, `${id}.txt`);

try {
  // yt-transcript-kit prints the transcript to stdout
  const text = execSync(
    `npx -y yt-transcript-kit ${id} --languages ${lang}`,
    { encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'], timeout: 120000 }
  ).trim();

  if (!text || text.length < 10) {
    console.error(`No transcript text returned for ${id} (lang=${lang}).`);
    process.exit(1);
  }

  fs.writeFileSync(outFile, text, 'utf8');
  console.error(`[saved] ${outFile} (${text.length} chars)`);
  process.stdout.write(text);
  process.exit(0);
} catch (err) {
  console.error(`FAILED to fetch transcript for ${id} (lang=${lang}).`);
  console.error('Likely cause: video has no captions in that language (e.g. Luganda videos).');
  console.error('Detail:', (err.stderr || err.message || '').toString().slice(0, 400));
  process.exit(1);
}
