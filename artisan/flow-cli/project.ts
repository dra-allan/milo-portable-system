/** flow project list/use/current: manage Flow projects. */
import { cli, Strategy } from '@jackwener/opencli/registry';
import { SITE, inFlowPage, flowFetch, loadState, saveState, classifyError } from './_shared.js';
import { timeFriendly, shortId } from './_format.js';

cli({
  site: SITE, name: 'project-list', description: 'List your Flow projects, newest first', access: 'read', defaultFormat: 'table', strategy: Strategy.COOKIE, browser: true, domain: 'labs.google', navigateBefore: false,
  args: [{ name: 'pageSize', type: 'number', default: 20, help: 'Number of projects to return (default 20)' }],
  columns: ['Project ID (short)', 'Project name', 'Created'],
  func: async (page, kwargs) => {
    await inFlowPage(page);
    const input = encodeURIComponent(JSON.stringify({ json: { pageSize: Number(kwargs.pageSize) || 20, toolName: 'PINHOLE', cursor: null }, meta: { values: { cursor: ['undefined'] } } }));
    const r = await flowFetch(page, `https://labs.google/fx/api/trpc/project.searchUserProjects?input=${input}`);
    if (!r.ok) throw classifyError(r.status, r.body);
    const projects = r.body?.result?.data?.json?.result?.projects ?? [];
    return projects.map((p: any) => ({ 'Project ID (short)': shortId(p.projectId), 'Project name': p.projectInfo?.projectTitle ?? '(Unnamed)', Created: timeFriendly(p.creationTime), projectId: p.projectId }));
  },
  footerExtra: () => 'Switch: `flow project-use --projectId <full UUID>`',
});

cli({
  site: SITE, name: 'project-current', description: 'Show the current default project', access: 'read', defaultFormat: 'table', strategy: Strategy.PUBLIC, browser: false, args: [], columns: ['Default project'],
  func: async () => { const s = loadState(); return [{ 'Default project': s.currentProjectId && s.currentProjectId !== 'undefined' ? s.currentProjectId : '(Not set: run `flow project-use --projectId <id>` or open a Flow project)', projectId: s.currentProjectId }]; },
});

cli({
  site: SITE, name: 'project-use', description: 'Set the default project for later Flow commands', access: 'write', defaultFormat: 'table', strategy: Strategy.PUBLIC, browser: false,
  args: [{ name: 'projectId', required: true, help: 'Full project UUID from flow project-list' }], columns: ['Selected project'],
  func: async (kwargs) => { const id = String(kwargs.projectId || ''); if (!id || id === 'undefined') throw new Error('Provide a full project UUID with --projectId'); const s = loadState(); s.currentProjectId = id; saveState(s); return [{ 'Selected project': id, projectId: id }]; },
});
