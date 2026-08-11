/**
 * flow image-batch — batch generate images from a prompt file with multi-account profile failover.
 *
 * Reads prompts from a file and executes flow image-gen for each, automatically
 * switching Chrome profiles (Google Accounts) when rate limits are encountered.
 */
import { cli, Strategy } from '@jackwener/opencli/registry';
import * as fs from 'node:fs';
import * as path from 'node:path';
import { execSync } from 'child_process';

cli({
  site: 'flow',
  name: 'image-batch',
  description: 'Batch generate images: read prompts from a file and generate each one (supports multi-account profile rotation to avoid rate limits)',
  access: 'write',
  defaultFormat: 'table',
  strategy: Strategy.COOKIE,
  browser: true,
  domain: 'labs.google',
  navigateBefore: false,
  args: [
    { name: 'file', required: true, help: 'Prompt file path (one prompt per line, or a JSON array)' },
    { name: 'model', help: 'Model; default nano-banana-2. Friendly aliases: nb2-lite, nb2, nb2-pro' },
    { name: 'count', type: 'int', default: 1, help: 'Samples per prompt; 1-4' },
    { name: 'refs', help: 'Reference image list (comma-separated), applied to all prompts. Example: --refs logo.png,background.jpg' },
    { name: 'seed', type: 'int', help: 'Base random seed; increments per prompt' },
    { name: 'output-dir', required: true, help: 'Directory to save the images' },
    { name: 'retry', type: 'boolean', default: true, help: 'Automatically retry on rate limits or server errors' },
    { name: 'max-retries', type: 'int', default: 3, help: 'Max retries (per account)' },
    { name: 'profiles', help: 'Google account profile list, comma-separated (e.g. "acc1,acc2,acc3"). Use `opencli profile list` to see available profiles' },
    { name: 'projectId', help: 'Project ID; falls back to flow project-use default or the current page' },
    { name: 'aspect', help: 'Aspect ratio: 1:1, 9:16, 16:9 (default 16:9)' },
  ],
  columns: ['progress', 'status', 'prompt', 'model', 'credits', 'note'],
  footerExtra: (kwargs) => {
    return `Prompt file: ${kwargs.file}\nOutput dir: ${kwargs['output-dir']}\nAccount profiles: ${kwargs.profiles || 'default'}`;
  },
  func: async (_, kwargs) => {
    const file = kwargs.file;
    const outDir = kwargs['output-dir'];

    // Ensure output directory exists
    if (!fs.existsSync(outDir)) {
      fs.mkdirSync(outDir, { recursive: true });
    }

    // Parse prompt file: either JSON array of objects or plain text lines
    const content = fs.readFileSync(file, 'utf8');
    let prompts: string[];
    try {
      const data = JSON.parse(content);
      if (Array.isArray(data)) {
        prompts = data.map((item: any) => item.prompt ?? '').filter((p: string) => p.length > 0);
      } else {
        // Not an array, treat as single prompt
        prompts = [content.trim()];
      }
    } catch {
      // Not JSON, treat as plain text lines
      prompts = content.split('\n').map(line => line.trim()).filter(line => line.length > 0);
    }

    if (prompts.length === 0) {
      throw new Error('Prompt file is empty or has no valid prompts');
    }

    // Parse profiles: comma-separated list of OpenCLI profile names
    const profileList = kwargs.profiles
      ? String(kwargs.profiles).split(',').map((p) => p.trim())
      : [null]; // null = default profile (no --profile flag)

    let currentProfileIdx = 0;
    let processed = 0;
    let succeeded = 0;
    let failed = 0;

    for (let i = 0; i < prompts.length; i++) {
      processed++;
      const promptText = prompts[i].trim();
      if (!promptText) {
        failed++;
        continue;
      }

      const modelKey = kwargs.model ? String(kwargs.model) : 'nano-banana-2';
      const count = Number(kwargs.count) || 1;
      const seedBase = Number(kwargs.seed) || Math.floor(Math.random() * 2_147_483_647);

      // Build base filename (will add _N suffix if count > 1)
      const baseName = `img_${i + 1}_${promptText.substring(0, 20).replace(/[^a-z0-9]/gi, '_')}`;
      const outPathBase = path.join(outDir, baseName);

      let success = false;
      let attempts = 0;
      const maxAttempts = profileList.length; // Try each profile at most once

      while (!success && attempts < maxAttempts) {
        const activeProfile = profileList[currentProfileIdx];
        const profileFlag = activeProfile ? `--profile ${activeProfile}` : '';
        const outFile = count > 1
          ? `${outPathBase}_${(attempt * count) + 1}.jpg` // Avoid overwriting if retrying
          : `${outPathBase}.jpg`;

        try {
          // Build the opencli flow image-gen command
          const cmd = [
            'opencli',
            profileFlag,
            'flow',
            'image-gen',
            `--prompt "${promptText.replace(/"/g, '\\"')}"`,
            `--model ${modelKey}`,
            `--count ${count}`,
            `--seed ${seedBase + i}`, // Vary seed per prompt
            kwargs.refs ? `--refs ${kwargs.refs}` : '',
            `--out "${outFile}"`,
            kwargs.aspect ? `--aspect ${kwargs.aspect}` : '',
            '--yes' // Skip dry-run confirmation
          ].filter(Boolean).join(' ');

          // Execute the command
          execSync(cmd, { stdio: 'inherit' });

          // Check if files were created as expected
          let filesFound = 0;
          for (let j = 1; j <= count; j++) {
            const expectedFile = count > 1
              ? `${outPathBase}_${j}.jpg`
              : `${outPathBase}.jpg`;
            if (fs.existsSync(expectedFile)) {
              filesFound++;
            }
          }

          if (filesFound > 0 || count === 1) {
            success = true;
            succeeded++;
          } else {
            throw new Error('Image generation command succeeded but no output files were found');
          }
        } catch (err: any) {
          console.warn(`\n[WARN] Rate limit or error on profile [${activeProfile || 'default'}]. Error: ${err.message}`);

          if (profileList.length > 1) {
            // Rotate to next Google Account profile
            currentProfileIdx = (currentProfileIdx + 1) % profileList.length;
            attempts++;
            console.log(`[INFO] Switching to Google account profile: [${profileList[currentProfileIdx]}]...`);
          } else {
            // No other accounts configured, re-throw the error
            throw err;
          }
        }
      }

      if (!success) {
        console.error(`[ERROR] Prompt #${i + 1} failed on all available account profiles.`);
        failed++;
      }
    }

    return [{
      progress: `${processed}/${prompts.length}`,
      status: succeeded > 0 ? 'done' : 'all failed',
      prompt: `ok: ${succeeded}, failed: ${failed}`,
      model: kwargs.model ?? 'nano-banana-2',
      credits: 'N/A (see individual generation output)',
      note: `Output dir: ${outDir}\nAccount profiles: ${profileList.filter(p => p !== null).join(', ') || 'default'}`,
    }];
  },
});
