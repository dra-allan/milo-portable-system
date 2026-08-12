/**
 * flow job-status / job-wait / job-download / job-list
 *
 * Read-side commands for generated videos. State is fetched from the trpc
 * flow.projectInitialData endpoint and completed MP4 files are downloaded
 * through the signed media URL endpoint.
 *
 * Copyright © 2026 Daada Allan.
 */
import * as fs from 'node:fs';
import { cli, Strategy } from '@jackwener/opencli/registry';
import { SITE, FLOW_ORIGIN, inFlowPage, flowFetch, loadState, classifyError } from './_shared.js';
import { statusFriendly, modelFriendly, aspectFriendly, shortId, timeFriendly, truncate } from './_format.js';

async function fetchProjectMedia(page: any, projectId: string) {
  const input = encodeURIComponent(JSON.stringify({ json: { projectId } }));
  const r = await flowFetch(page, `${FLOW_ORIGIN}/fx/api/trpc/flow.projectInitialData?input=${input}`);
  if (!r.ok) throw classifyError(r.status, r.body);
  const data = r.body?.result?.data?.json?.projectContents;
  return { media: (data?.media ?? []) as any[], workflows: (data?.workflows ?? []) as any[] };
}

function summarizeMediaFriendly(m: any, opts: { promptMax?: number } = {}) {
  const reqCtl = m.mediaMetadata?.requestData?.videoGenerationRequestData?.videoModelControlInput;
  const statusRaw = m.mediaMetadata?.mediaStatus?.mediaGenerationStatus ?? 'NONE';
  const fullPrompt = m.mediaMetadata?.mediaTitle ?? '';
  return {
    'Job ID': shortId(m.name), Status: statusFriendly(statusRaw), Prompt: opts.promptMax ? truncate(fullPrompt, opts.promptMax) : fullPrompt,
    Model: modelFriendly(reqCtl?.videoModelName), 'Aspect ratio': aspectFriendly(reqCtl?.videoAspectRatio), Length: m.video?.dimensions?.length ?? '', Created: timeFriendly(m.mediaMetadata?.createTime),
    mediaId: m.name, workflowId: m.workflowId, status_raw: statusRaw, model_raw: reqCtl?.videoModelName, aspect_raw: reqCtl?.videoAspectRatio, prompt_full: fullPrompt,
  };
}

cli({ site: SITE, name: 'job-status', description: 'Show the current status of a generation job', access: 'read', defaultFormat: 'table', strategy: Strategy.COOKIE, browser: true, domain: 'labs.google', navigateBefore: false,
  args: [{ name: 'mediaId', required: true, help: 'Generation job ID returned by flow gen' }, { name: 'projectId', help: 'Project ID; defaults to the selected project' }],
  columns: ['Job ID', 'Status', 'Prompt', 'Model', 'Aspect ratio', 'Length', 'Created'],
  func: async (page, kwargs) => { const { projectId: urlProjectId } = await inFlowPage(page); const projectId = String(kwargs.projectId || loadState().currentProjectId || urlProjectId || ''); if (!projectId) throw new Error('No project ID. Run flow project-use or open a Flow project first'); const { media } = await fetchProjectMedia(page, projectId); const m = media.find((x) => x.name === kwargs.mediaId); if (!m) return [{ 'Job ID': shortId(String(kwargs.mediaId)), Status: '❓ Not found', Prompt: 'The job may still be queued or may not belong to this project', Model: '', 'Aspect ratio': '', Length: '', Created: '', mediaId: kwargs.mediaId, status_raw: 'NOT_FOUND' }]; return [summarizeMediaFriendly(m)]; },
  footerExtra: (kwargs) => `Download when complete: \`flow job-download --mediaId ${kwargs.mediaId} --out out.mp4\``,
});

cli({ site: SITE, name: 'job-wait', description: 'Wait for a video generation job to finish', access: 'read', defaultFormat: 'table', strategy: Strategy.COOKIE, browser: true, domain: 'labs.google', navigateBefore: false,
  args: [{ name: 'mediaId', required: true, help: 'Generation job ID' }, { name: 'timeoutSeconds', type: 'int', default: 300, help: 'Maximum wait in seconds; default 300 (5 minutes)' }, { name: 'pollSeconds', type: 'int', default: 10, help: 'Polling interval in seconds; default 10' }, { name: 'projectId', help: 'Project ID; defaults to the selected project' }],
  columns: ['Job ID', 'Final status', 'Prompt', 'Wait seconds'],
  func: async (page, kwargs) => { const { projectId: urlProjectId } = await inFlowPage(page); const projectId = String(kwargs.projectId || loadState().currentProjectId || urlProjectId || ''); if (!projectId) throw new Error('No project ID'); const mediaId = String(kwargs.mediaId); const timeoutMs = Math.max(5, Number(kwargs.timeoutSeconds) || 300) * 1000; const pollMs = Math.max(2, Number(kwargs.pollSeconds) || 10) * 1000; const start = Date.now(); while (true) { const { media } = await fetchProjectMedia(page, projectId); const m = media.find((x) => x.name === mediaId); const status = m?.mediaMetadata?.mediaStatus?.mediaGenerationStatus ?? 'NOT_FOUND'; const elapsed = Math.round((Date.now() - start) / 1000); const ended = ['MEDIA_GENERATION_STATUS_SUCCESSFUL', 'MEDIA_GENERATION_STATUS_FAILED', 'MEDIA_GENERATION_STATUS_CANCELLED'].includes(status); if (ended) return [{ 'Job ID': shortId(mediaId), 'Final status': statusFriendly(status), Prompt: (m?.mediaMetadata?.mediaTitle ?? '').slice(0, 50), 'Wait seconds': elapsed, mediaId, status_raw: status }]; if (Date.now() - start >= timeoutMs) return [{ 'Job ID': shortId(mediaId), 'Final status': `⏱️ Timed out (current: ${statusFriendly(status)})`, Prompt: (m?.mediaMetadata?.mediaTitle ?? '').slice(0, 50), 'Wait seconds': elapsed, mediaId, status_raw: 'TIMEOUT_' + status }]; await new Promise((res) => setTimeout(res, pollMs)); } },
  footerExtra: (kwargs) => `Download: \`flow job-download --mediaId ${kwargs.mediaId} --out out.mp4\``,
});

cli({ site: SITE, name: 'job-download', description: 'Download a completed MP4 video', access: 'read', defaultFormat: 'table', strategy: Strategy.COOKIE, browser: true, domain: 'labs.google', navigateBefore: false,
  args: [{ name: 'mediaId', required: true, help: 'Generation job ID' }, { name: 'out', required: true, help: 'Output path, including the .mp4 filename' }], columns: ['Job ID', 'Saved to', 'Size'],
  func: async (page, kwargs) => { const mediaId = String(kwargs.mediaId); const outPath = String(kwargs.out); await inFlowPage(page); const redirUrl = `${FLOW_ORIGIN}/fx/api/trpc/media.getMediaUrlRedirect?name=${encodeURIComponent(mediaId)}`; const probeJs = `(async () => { const r = await fetch(${JSON.stringify(redirUrl)}); return { ok: r.ok, status: r.status, finalUrl: r.url, ct: r.headers.get('content-type') || '' }; })()`; const probe = (await page.evaluate(probeJs)) as any; if (!probe?.ok || !probe?.finalUrl) throw new Error(`getMediaUrlRedirect failed: ${JSON.stringify(probe)}`); const dl = await fetch(probe.finalUrl); if (!dl.ok) throw new Error(`Signed download failed: status=${dl.status}`); const bytes = Buffer.from(await dl.arrayBuffer()); fs.writeFileSync(outPath, bytes); return [{ 'Job ID': shortId(mediaId), 'Saved to': outPath, Size: `${(bytes.length / 1024).toFixed(1)} KB`, mediaId, sizeBytes: bytes.length }]; },
  footerExtra: (kwargs) => `Preview locally: \`open ${kwargs.out}\``,
});

cli({ site: SITE, name: 'job-list', description: 'List video generation jobs for the current project', access: 'read', defaultFormat: 'table', strategy: Strategy.COOKIE, browser: true, domain: 'labs.google', navigateBefore: false,
  args: [{ name: 'projectId', help: 'Project ID; defaults to the selected project' }, { name: 'limit', type: 'int', default: 20, help: 'Number of results, newest first' }, { name: 'status', help: 'Filter: done, failed, or pending; omit to show all' }, { name: 'full', type: 'boolean', default: false, help: 'Do not truncate prompts; useful with -f csv' }], columns: ['Job ID', 'Status', 'Prompt', 'Model', 'Aspect ratio', 'Length', 'Created'],
  func: async (page, kwargs) => { const { projectId: urlProjectId } = await inFlowPage(page); const projectId = String(kwargs.projectId || loadState().currentProjectId || urlProjectId || ''); if (!projectId) throw new Error('No project ID'); const { media } = await fetchProjectMedia(page, projectId); const videos = media.filter((m) => m.video); const filterMap: Record<string, string> = { done: 'MEDIA_GENERATION_STATUS_SUCCESSFUL', success: 'MEDIA_GENERATION_STATUS_SUCCESSFUL', failed: 'MEDIA_GENERATION_STATUS_FAILED', pending: 'MEDIA_GENERATION_STATUS_SCHEDULED' }; const wantStatus = kwargs.status ? (filterMap[String(kwargs.status)] || String(kwargs.status)) : null; const filtered = wantStatus ? videos.filter((m) => m.mediaMetadata?.mediaStatus?.mediaGenerationStatus === wantStatus) : videos; const sorted = [...filtered].sort((a, b) => (b.mediaMetadata?.createTime || '').localeCompare(a.mediaMetadata?.createTime || '')); const promptMax = kwargs.full ? undefined : 28; return sorted.slice(0, Number(kwargs.limit) || 20).map((m) => summarizeMediaFriendly(m, { promptMax })); },
  footerExtra: (kwargs) => kwargs.full ? 'Full prompts are shown; use -f csv for easy copying' : 'Prompts are truncated to 28 characters; add --full for complete text',
});
