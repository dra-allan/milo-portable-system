/** flow credits: show the current Flow credit balance and tier. */
import { cli, Strategy } from '@jackwener/opencli/registry';
import { SITE, FLOW_BASE, inFlowPage, flowFetch, classifyError } from './_shared.js';
import { tierFriendly } from './_format.js';

const FLOW_API_KEY = '***REMOVED***';

cli({
  site: SITE,
  name: 'credits',
  description: 'Show current Flow credit balance and account tier',
  access: 'read', defaultFormat: 'table', strategy: Strategy.COOKIE,
  browser: true, domain: 'labs.google', navigateBefore: false, args: [],
  columns: ['Available credits', 'Subscription credits', 'Account tier', 'Service tier'],
  func: async (page, _kwargs) => {
    await inFlowPage(page);
    const r = await flowFetch(page, `${FLOW_BASE}/credits?key=${FLOW_API_KEY}`);
    if (!r.ok) throw classifyError(r.status, r.body);
    return [{
      'Available credits': r.body.credits,
      'Subscription credits': r.body.subscriptionCredits,
      'Account tier': tierFriendly(r.body.userPaygateTier) + (r.body.sku ? ` (${r.body.sku})` : ''),
      'Service tier': tierFriendly(r.body.serviceTier),
      credits: r.body.credits, sku: r.body.sku,
      userPaygateTier_raw: r.body.userPaygateTier,
    }];
  },
  footerExtra: () => 'Preview cost: `flow gen --prompt "..." --length 4 --dryRun true`',
});
