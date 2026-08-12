/** flow models: list Google Omni model variants in an English table. */
import { cli, Strategy } from '@jackwener/opencli/registry';
import { SITE } from './_shared.js';
import { OMNI } from './_models.js';
import { modeLabel } from './_format.js';

cli({
  site: SITE,
  name: 'models',
  description: 'Show supported Google Omni video modes and credit prices',
  access: 'read', defaultFormat: 'table', strategy: Strategy.PUBLIC,
  browser: false,
  args: [{ name: 'raw', type: 'boolean', default: false, help: 'Show raw model keys such as abra_t2v_4s' }],
  columns: ['Mode', 'Alias (--model)', 'Length', 'Aspect ratio', 'Credits', 'Max reference images', 'Max audio refs', 'Max characters'],
  func: async (kwargs) => {
    if (kwargs.raw) return Object.values(OMNI).map((m) => ({ key: m.key, mode: m.mode, length_s: m.lengthSeconds || (m.key === 'abra_edit' ? '≤10 input' : '-'), credits: m.cost, max_img_incl_chars: m.maxImagesInclChars, max_audio: m.maxAudioRefs, max_chars: m.maxCharacters, key_raw: m.key }));
    const aliasOf = (key: string) => key === 'abra_edit' ? 'edit' : key.replace(/^abra_/, '').replace(/_/g, '-');
    return Object.values(OMNI).map((m) => ({
      Mode: modeLabel(m.mode), 'Alias (--model)': aliasOf(m.key), Length: m.key === 'abra_edit' ? 'Matches input (≤10s)' : `${m.lengthSeconds}s`, 'Aspect ratio': '9:16 / 16:9', Credits: m.cost,
      'Max reference images': m.maxImagesInclChars > 0 ? m.maxImagesInclChars : '—', 'Max audio refs': m.maxAudioRefs > 0 ? m.maxAudioRefs : '—', 'Max characters': m.maxCharacters > 0 ? m.maxCharacters : '—', key_raw: m.key,
    }));
  },
  footerExtra: () => 'Example: `flow gen --prompt "..." --length 8 --refs cat.png,bg.jpg --yes`',
});
