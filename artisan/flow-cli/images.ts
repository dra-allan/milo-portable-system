/**
 * flow images — ONE command to turn a POV batch prompt file into images.
 *
 * Reads a prompt file (default: 05_IMAGES/IMAGE_PROMPTS_BATCH_FINAL.txt in the
 * current directory), finds every prompt block with a segment ID like
 * [NAR-042] or [NAR-042-B], generates each image via Flow, and saves it as
 * <SEG_ID>.jpeg in the target folder — exactly what the POV assembler expects.
 *
 * Everything a person needs is defaulted. You just run:
 *     opencli flow images
 *
 * Flags are all optional tweaks, not requirements.
 */
import { cli, Strategy } from '@jackwener/opencli/registry';
import * as fs from 'node:fs';
import * as path from 'node:path';
import { execSync } from 'child_process';

/** Match a segment ID block start, e.g. [NAR-042] or [NAR-042-B]. */
const SEG_ID_RE = /^\s*\[([A-Z0-9]{2,8}-\d{3}(?:-[A-E])?)\]\s*(.*)$/;

/**
 * Parse a POV batch prompt file into [{ id, prompt }].
 *
 * The batch files are noisy: they contain GOOGLE FLOW AUTOMATION DIRECTIVE
 * headers, CHARACTER REGISTRY blocks, PROMPT SUMMARY lines and
 * "--- BATCH X ---" separators. A real prompt is any block that opens with a
 * segment ID in brackets. Registry entries like [MAIN] never match because
 * they have no digits.
 */
export function parseBatchPrompts(content: string): Array<{ id: string; prompt: string }> {
  const blocks = content.split(/\n\s*\n/);
  const out: Array<{ id: string; prompt: string }> = [];
  const seen = new Set<string>();

  for (const block of blocks) {
    const trimmed = block.trim();
    if (!trimmed) continue;
    const m = trimmed.match(SEG_ID_RE);
    if (!m) continue; // header / registry / summary noise
    const id = m[1];
    // Strip the leading "[ID] - Short snippet | " prefix so the model gets
    // clean prompt text. Keep the rest of the block untouched.
    const body = trimmed.replace(/^\[[^\]]+\]\s*-\s*[^|]*\|\s*/, '').trim();
    if (body.length < 10) continue; // sanity: a real prompt has actual content
    if (seen.has(id)) continue;     // dupes in the file → keep first
    seen.add(id);
    out.push({ id, prompt: body });
  }

  return out;
}

cli({
  site: 'flow',
  name: 'images',
  description: 'Generate images from a POV batch prompt file (05_IMAGES/IMAGE_PROMPTS_BATCH_FINAL.txt) — saves <SEG_ID>.jpeg for the assembler',
  access: 'write',
  defaultFormat: 'table',
  strategy: Strategy.COOKIE,
  browser: true,
  domain: 'labs.google',
  navigateBefore: false,
  args: [
    { name: 'file', help: 'Prompt batch file; default: 05_IMAGES/IMAGE_PROMPTS_BATCH_FINAL.txt in the current directory' },
    { name: 'out', help: 'Output directory; default: same folder as the prompt file' },
    { name: 'model', help: 'Model; default nano-banana-2. Aliases: nb2-lite, nb2, nb2-pro' },
    { name: 'aspect', help: 'Aspect ratio: 16:9, 9:16, 1:1 (default 16:9 — the POV standard)' },
    { name: 'profiles', help: 'Google account profiles to rotate through on rate limits, comma-separated (e.g. acc1,acc2)' },
    { name: 'force', type: 'boolean', default: false, help: 'Re-generate images that already exist (default: skip them)' },
    { name: 'max', type: 'int', help: 'Stop after generating this many images (useful for a quick test)' },
    { name: 'dryRun', type: 'boolean', default: false, help: 'Only parse and show what would be generated — no requests' },
  ],
  columns: ['done', 'id', 'status', 'note'],
  func: async (_, kwargs) => {
    const cwd = process.cwd();
    const file = kwargs.file
      ? path.resolve(cwd, String(kwargs.file))
      : path.resolve(cwd, '05_IMAGES', 'IMAGE_PROMPTS_BATCH_FINAL.txt');

    if (!fs.existsSync(file)) {
      throw new Error(
        `Prompt file not found: ${file}\n` +
        `Run this inside your POV project folder (where 05_IMAGES/ lives), or pass --file <path> to the batch file.`,
      );
    }

    const content = fs.readFileSync(file, 'utf8');
    const prompts = parseBatchPrompts(content);

    if (prompts.length === 0) {
      throw new Error(`No prompt blocks with segment IDs found in ${file}`);
    }

    const outDir = kwargs.out
      ? path.resolve(cwd, String(kwargs.out))
      : path.dirname(file);
    if (!fs.existsSync(outDir)) {
      fs.mkdirSync(outDir, { recursive: true });
    }

    // Work out what already exists → skip unless --force.
    const existing = new Set(
      fs.readdirSync(outDir)
        .map((n) => n.replace(/\.(jpe?g|png|webp|bmp)$/i, ''))
        .filter(Boolean),
    );

    const model = kwargs.model ? String(kwargs.model) : 'nano-banana-2';
    const aspect = kwargs.aspect ? String(kwargs.aspect) : '16:9';
    const profileList = kwargs.profiles
      ? String(kwargs.profiles).split(',').map((p) => p.trim()).filter(Boolean)
      : [];
    const max = kwargs.max ? Number(kwargs.max) : Infinity;
    const force = Boolean(kwargs.force);

    const todo = prompts.filter((p) => force || !existing.has(p.id));

    if (kwargs.dryRun) {
      const skipped = prompts.length - todo.length;
      return [{
        done: `parsed ${prompts.length}, to generate ${todo.length}, skip (already exist) ${skipped}`,
        id: todo.length ? todo[0].id : '-',
        status: 'dry run — nothing submitted',
        note: `out: ${outDir} | model: ${model} | aspect: ${aspect}${profileList.length ? ' | profiles: ' + profileList.join(',') : ''}`,
      }];
    }

    if (todo.length === 0) {
      return [{ done: `all ${prompts.length} images already exist`, id: '-', status: 'nothing to do', note: `Use --force to re-generate` }];
    }

    let currentProfileIdx = 0;
    let ok = 0;
    let failed = 0;
    const rows: any[] = [];
    const results: any[] = [];

    for (let i = 0; i < todo.length && ok + failed < max; i++) {
      const { id, prompt } = todo[i];
      const outFile = path.join(outDir, `${id}.jpeg`);
      console.log(`\n[${ok + failed + 1}/${Math.min(todo.length, max)}] ${id}`);

      let success = false;
      let attempts = 0;
      const maxAttempts = Math.max(1, profileList.length);

      while (!success && attempts < maxAttempts) {
        const activeProfile = profileList.length ? profileList[currentProfileIdx] : null;
        const profileFlag = activeProfile ? `--profile ${activeProfile}` : '';
        try {
          const cmd = [
            'opencli', profileFlag, 'flow', 'image-gen',
            `--prompt "${prompt.replace(/"/g, '\\"')}"`,
            `--model ${model}`,
            `--count 1`,
            `--aspect ${aspect}`,
            `--out "${outFile}"`,
            '--yes',
          ].filter(Boolean).join(' ');
          execSync(cmd, { stdio: 'inherit' });
          success = fs.existsSync(outFile);
          if (!success) throw new Error('command finished but no file was saved');
          ok++;
          results.push({ id, status: 'done', note: `saved ${path.basename(outFile)}` });
        } catch (err: any) {
          if (profileList.length > 1) {
            currentProfileIdx = (currentProfileIdx + 1) % profileList.length;
            attempts++;
            console.log(`[rotate] ${id} failed on [${activeProfile}], trying [${profileList[currentProfileIdx]}]`);
          } else {
            failed++;
            results.push({ id, status: 'FAILED', note: err.message });
            attempts = maxAttempts;
          }
        }
      }

      if (success === false && results[results.length - 1]?.id !== id) {
        failed++;
        results.push({ id, status: 'FAILED', note: 'all profiles exhausted' });
      }
    }

    rows.push({
      done: `generated ${ok}, failed ${failed}, skipped ${prompts.length - todo.length}`,
      id: results.length ? results[0].id : '-',
      status: failed > 0 ? 'partial' : 'done',
      note: `images in: ${outDir}`,
    });
    for (const r of results) {
      rows.push({ done: '', ...r });
    }
    return rows;
  },
});
