/**
 * flow image-gen — generate an image using Google Flow's Imagen/Nano Banana models.
 *
 * Uses the synchronous flowMedia:batchGenerateImages endpoint.
 */
import { cli, Strategy } from '@jackwener/opencli/registry';
import * as crypto from 'node:crypto';
import * as fs from 'node:fs';
import * as path from 'node:path';
import {
  SITE, FLOW_BASE, inFlowPage, flowFetch, getRecaptchaToken, classifyError, FlowError, loadState,
} from './_shared.js';
import {
  resolveRefToken,
  type MediaCacheEntry,
} from './media.js';
import { modelFriendly, shortId } from './_format.js';
import { pickImageModel, totalImageCost, type ImageModelSpec } from './_images.js';

function uuid(): string {
  return (crypto as any).randomUUID ? (crypto as any).randomUUID() : crypto.randomBytes(16).toString('hex');
}

interface ImageGenInputs {
  prompt: string;
  count: number;
  refs: string[];
  seed?: number;
}

/**
 * Map user-friendly aspect ratios to Flow enum strings.
 */
const ASPECT_MAP: Record<string, string> = {
  '1:1': 'IMAGE_ASPECT_RATIO_SQUARE',
  '9:16': 'IMAGE_ASPECT_RATIO_PORTRAIT',
  '16:9': 'IMAGE_ASPECT_RATIO_LANDSCAPE',
};

/**
 * Map user-friendly model names to the imageModelName expected by the API.
 * Extend as needed.
 */
const IMAGE_MODEL_ALIASES: Record<string, string> = {
  // Imagen 4
  'imagen-4': 'IMAGEN_4',
  'imagen4': 'IMAGEN_4',
  // Nano Banana 2 Lite
  'nano-banana-2-lite': 'GEM_PIX_2_LITE',
  'nb2-lite': 'GEM_PIX_2_LITE',
  // Nano Banana 2
  'nano-banana-2': 'GEM_PIX_2',
  'nb2': 'GEM_PIX_2',
  // Nano Banana 2 Pro
  'nano-banana-2-pro': 'GEM_PIX_2_PRO',
  'nb2-pro': 'GEM_PIX_2_PRO',
  // Fallback: if user passes raw enum, pass through
  'GEM_PIX_2_LITE': 'GEM_PIX_2_LITE',
  'GEM_PIX_2': 'GEM_PIX_2',
  'GEM_PIX_2_PRO': 'GEM_PIX_2_PRO',
  'IMAGEN_4': 'IMAGEN_4',
};

function resolveImageModel(input: string): string {
  return IMAGE_MODEL_ALIASES[input] || input;
}

/**
 * Sleep for ms milliseconds.
 */
function sleep(ms: number): Promise<void> {
  return new Promise(resolve => setTimeout(resolve, ms));
}

cli({
  site: SITE,
  name: 'image-gen',
  description: 'Generate an image with Google Flow (text-to-image, or image-reference with --refs: alias / local path / mediaId)',
  access: 'write',
  defaultFormat: 'table',
  strategy: Strategy.COOKIE,
  browser: true,
  domain: 'labs.google',
  navigateBefore: false,
  args: [
    { name: 'prompt', required: true, help: 'The image prompt' },
    { name: 'model', help: 'Model; default nano-banana-2. Friendly aliases: nb2-lite, nb2, nb2-pro, imagen-4' },
    { name: 'count', type: 'int', default: 1, help: 'Number of samples per prompt; 1-4' },
    { name: 'refs', help: 'Reference image list, comma-separated; each token can be an alias, local path, or mediaId UUID. Example: --refs cat,./bg.jpg,9a42af9d-...' },
    { name: 'seed', type: 'int', help: 'Random seed, random by default' },
    { name: 'dryRun', type: 'boolean', default: false, help: 'Only estimate the credit cost, do not send a real request' },
    { name: 'yes', type: 'boolean', default: false, help: 'Skip the dry-run confirmation and submit directly (for agents)' },
    { name: 'reload', type: 'boolean', default: false, help: 'Reload the Flow page before submitting (get a fresh reCAPTCHA / session to avoid PUBLIC_ERROR_UNUSUAL_ACTIVITY)' },
    { name: 'projectId', help: 'Project ID; falls back to flow project-use default or the current page' },
    { name: 'retry', type: 'boolean', default: true, help: 'Automatically retry on rate limits or server errors' },
    { name: 'max-retries', type: 'int', default: 3, help: 'Max retry count' },
    { name: 'out', help: 'Output file path (if count>1 an index is appended, e.g. out_1.jpg, out_2.jpg)' },
    { name: 'aspect', help: 'Aspect ratio: 1:1, 9:16, 16:9 (default 16:9)' },
  ],
  columns: ['status', 'mediaId', 'model', 'credits', 'balance', 'note'],
  footerExtra: (kwargs) => {
    if (kwargs.dryRun) return 'Add --yes to actually submit; without --yes this is only a preview and costs nothing';
    if (!kwargs.yes) return 'If this looks right, re-run with the same args plus --yes to submit';
    return kwargs.out
      ? `Images will be saved to: ${kwargs.out}${(Number(kwargs.count) || 1) > 1 ? ' (an index will be appended)' : ''}`
      : 'Track/download: `flow image-download --mediaId <ID> --out out.jpg`';
  },
  func: async (page, kwargs) => {
    const prompt = String(kwargs.prompt || '').trim();
    if (!prompt) throw new Error('--prompt is required');
    const count = Math.max(1, Math.min(Number(kwargs.count) || 1, 4)); // clamp 1-4
    const refsRaw = kwargs.refs ? String(kwargs.refs).trim() : '';
    const refTokens = refsRaw ? refsRaw.split(',').map((s) => s.trim()).filter(Boolean) : [];
    const seed = Number.isFinite(Number(kwargs.seed)) ? Number(kwargs.seed) : Math.floor(Math.random() * 2_147_483_647);

    const modelKey = resolveImageModel(kwargs.model ? String(kwargs.model) : 'nano-banana-2');
    const model: ImageModelSpec = pickImageModel(modelKey); // validates and returns spec with cost etc.
    const cost = totalImageCost(model, count);

    const { projectId: pageProjectId } = await inFlowPage(page);
    const projectId = String(kwargs.projectId || loadState().currentProjectId || pageProjectId || '');
    if (!projectId) {
      throw new Error('No project ID. Run `flow project-use` first or open a project in the Flow web app');
    }

    // Resolve each ref token → mediaId (auto-uploads local file paths via cache).
    const refMediaIds: string[] = [];
    const refSources: string[] = [];
    for (const tok of refTokens) {
      const r = await resolveRefToken(page, tok, projectId);
      refMediaIds.push(r.mediaId);
      refSources.push(`${tok}->${r.source}`);
    }

    // Always check current balance + tier for the preview.
    const balResp = await flowFetch(page, `${FLOW_BASE}/credits?key=***REMOVED***`);
    const balance = balResp.ok ? Number(balResp.body?.credits ?? 0) : 0;
    const userPaygateTier: string = balResp.body?.userPaygateTier ?? 'PAYGATE_TIER_ONE';

    const noteParts: string[] = [];
    if (refMediaIds.length > 0) noteParts.push(`refs: ${refMediaIds.length} (${refSources.join(' / ')})`);
    const refNote = noteParts.length ? noteParts.join('; ') : 'no reference images';

    if (kwargs.dryRun) {
      return [{
        status: 'estimate',
        mediaId: '-',
        model: modelFriendly(model.key),
        credits: cost,
        balance: `${balance} -> ${balance - cost} (if submitted)`,
        note: `dry run, not submitted; ${refNote}`,
        ok: true, model_raw: model.key, cost_raw: cost, refMediaIds,
      }];
    }

    if (!kwargs.yes) {
      return [{
        status: 'pending-confirmation',
        mediaId: '-',
        model: modelFriendly(model.key),
        credits: cost,
        balance: `${balance} -> ${balance - cost} (if submitted)`,
        note: `add --yes to submit; ${refNote}`,
        ok: false, model_raw: model.key, cost_raw: cost, refMediaIds,
      }];
    }

    if (cost > balance) {
      throw new FlowError('INSUFFICIENT_CREDITS', `need ${cost} credits, have ${balance}`, false);
    }

    // Optional reload to refresh reCAPTCHA / session state.
    if (kwargs.reload) {
      const cur = page.getCurrentUrl ? await page.getCurrentUrl() : null;
      if (cur) await page.goto(cur, { settleMs: 2500 });
      await page.wait({ time: 1 });
    }

    let recaptchaToken = await getRecaptchaToken(page);
    const batchId = uuid();
    const baseSeed = seed;

    const maxRetries = Number(kwargs['max-retries']) || 3;
    const doRetry = Boolean(kwargs.retry);

    let lastError: any = null;
    for (let attempt = 0; attempt <= maxRetries; attempt++) {
      try {
        // Build requests array
        const requests = Array.from({ length: count }, (_, i) => {
          const requestSeed = baseSeed + i;
          const req: any = {
            clientContext: {
              projectId,
              tool: 'PINHOLE',
              sessionId: ';' + Date.now(),
            },
            prompt,
            seed: requestSeed,
            imageModelName: modelKey, // use the resolved model name (e.g., GEM_PIX_2)
            imageAspectRatio: ASPECT_MAP[kwargs.aspect ?? '16:9'] ?? 'IMAGE_ASPECT_RATIO_LANDSCAPE',
            imageInputs: refMediaIds.map((mid) => ({
              name: mid,
              imageInputType: 'IMAGE_INPUT_TYPE_BASE_IMAGE',
            })),
          };
          return req;
        });

        const payload = {
          clientContext: {
            projectId,
            tool: 'PINHOLE',
            sessionId: ';' + Date.now(),
            recaptchaContext: {
              applicationType: 'RECAPTCHA_APPLICATION_TYPE_WEB',
              token: recaptchaToken,
            },
          },
          requests,
        };

        const url = `${FLOW_BASE}/projects/${projectId}/flowMedia:batchGenerateImages`;
        const r = await flowFetch(page, url, {
          method: 'POST',
          body: payload,
        });
        if (!r.ok) throw classifyError(r.status, r.body);

        const media = r.body?.media ?? [];
        if (media.length === 0) {
          throw new Error('Image generation succeeded but the media list is empty');
        }

        const results = media.map((item: any, index: number) => {
          const img = item.image?.generatedImage || {};
          const mediaId = img.mediaId;
          const downloadUrl = img.fifeUrl ?? '';
          return {
            mediaId,
            downloadUrl,
            index,
          };
        });

        // If out path provided, download images
        const outPath = kwargs.out;
        if (outPath) {
          const outDir = path.dirname(outPath);
          if (outDir && !fs.existsSync(outDir)) {
            fs.mkdirSync(outDir, { recursive: true });
          }
          const ext = path.extname(outPath) || '.jpg';
          const baseName = path.basename(outPath, ext);
          for (const res of results) {
            const filename = count > 1
              ? `${baseName}_${res.index + 1}${ext}`
              : `${baseName}${ext}`;
            const fullPath = path.isAbsolute(filename) ? filename : path.join(process.cwd(), filename);
            // Download image
            const imgResp = await fetch(res.downloadUrl);
            if (!imgResp.ok) {
              throw new Error(`Image download failed: ${imgResp.status} ${imgResp.statusText}`);
            }
            const buffer = Buffer.from(await imgResp.arrayBuffer());
            fs.writeFileSync(fullPath, buffer);
          }
        }

        // For simplicity, we return first image's mediaId as primary (could also return array).
        const primary = results[0];
        return [{
          status: 'done',
          mediaId: shortId(primary.mediaId),
          model: modelFriendly(model.key),
          credits: cost,
          balance: r.body?.remainingCredits ?? `~${balance - cost}`,
          note: `generated ${results.length} image(s)${outPath ? ', saved to output path' : ''}`,
          // raw for agent
          ok: true,
          mediaId: primary.mediaId,
        }];
      } catch (err: any) {
        lastError = err;
        if (!doRetry) throw err;
        const flowErr = err instanceof FlowError ? err : classifyError(err.status ?? 0, err.body ?? {});
        if (!flowErr.retryable) {
          // Not retryable (e.g., auth, content policy)
          throw err;
        }
        if (attempt === maxRetries) {
          // Max retries reached
          throw new FlowError(
            flowErr.code,
            `Max retries reached (${maxRetries}): ${flowErr.message}`,
            flowErr.retryable,
            flowErr.details,
          );
        }
        // Exponential backoff with jitter
        const delayMs = Math.min(1000 * 2 ** attempt + Math.random() * 1000, 30000); // cap 30s
        // eslint-disable-next-line no-await-in-loop
        await sleep(delayMs);
        // Optionally reload session before retry
        if (kwargs.reload) {
          const cur = page.getCurrentUrl ? await page.getCurrentUrl() : null;
          if (cur) await page.goto(cur, { settleMs: 2500 });
          await page.wait({ time: 1 });
        }
        // Refresh recaptcha token for next attempt
        // eslint-disable-next-line no-await-in-loop
        recaptchaToken = await getRecaptchaToken(page);
      }
    }
    // If we exit loop without returning, throw last error
    throw lastError;
  },
});
