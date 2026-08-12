/** User-facing English labels for Flow status, model, and aspect enums. */
import { OMNI, type ModelSpec } from './_models.js';

export function modelFriendly(key: string): string {
  const m = OMNI[key];
  if (!m) return key;
  if (m.key === 'abra_edit') return 'Omni · Video editing';
  const modeLabel = m.mode === 't2v' ? 'text' : 'reference image';
  return `Omni · ${m.lengthSeconds}s · ${modeLabel}`;
}

export function modeLabel(mode: ModelSpec['mode']): string {
  return mode === 't2v' ? 'Text to video' : mode === 'r2v' ? 'Reference image to video' : 'Video editing';
}

export function aspectFriendly(v: string | undefined): string {
  if (!v) return '';
  if (v.includes('PORTRAIT')) return '9:16 portrait';
  if (v.includes('LANDSCAPE')) return '16:9 landscape';
  return v;
}

export function statusFriendly(v: string | undefined): string {
  switch (v) {
    case 'MEDIA_GENERATION_STATUS_SUCCESSFUL': return '✅ Complete';
    case 'MEDIA_GENERATION_STATUS_FAILED': return '❌ Failed';
    case 'MEDIA_GENERATION_STATUS_SCHEDULED': return '⏳ Queued';
    case 'MEDIA_GENERATION_STATUS_PROCESSING':
    case 'MEDIA_GENERATION_STATUS_ACTIVE': return '⚙️ Generating';
    case 'MEDIA_GENERATION_STATUS_PENDING': return '⏳ Waiting';
    case 'NOT_FOUND': return '❓ Not found';
    default: return v || '';
  }
}

export function tierFriendly(v: string | undefined): string {
  switch (v) {
    case 'PAYGATE_TIER_ONE': return 'Pro';
    case 'PAYGATE_TIER_TWO': return 'Ultra';
    case 'SERVICE_TIER_ADVANCED': return 'Advanced';
    case 'SERVICE_TIER_INTERMEDIATE': return 'Standard';
    case 'SERVICE_TIER_ENTRY': return 'Entry';
    default: return v || '';
  }
}

export function shortId(id: string | undefined): string { return (id || '').slice(0, 8); }

export function truncate(s: string | undefined, max: number): string {
  if (!s) return '';
  if (s.length <= max) return s;
  return s.slice(0, Math.max(0, max - 1)) + '…';
}

export function timeFriendly(iso: string | undefined): string {
  if (!iso) return '';
  const d = new Date(iso);
  if (isNaN(d.getTime())) return iso;
  const mm = String(d.getMonth() + 1).padStart(2, '0');
  const dd = String(d.getDate()).padStart(2, '0');
  const hh = String(d.getHours()).padStart(2, '0');
  const mi = String(d.getMinutes()).padStart(2, '0');
  return `${mm}-${dd} ${hh}:${mi}`;
}
