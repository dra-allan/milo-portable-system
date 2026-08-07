const fs = require('fs');
const path = require('path');
const https = require('https');

const storageDir = path.resolve(process.env.AGENTMEMORY_HOME || 'C:/Users/user/.milo/storage');
const storeFile = path.join(storageDir, 'agent-memory-store.json');
const storeBackup = storeFile + '.bak';
const vaultDir = 'C:/Users/user/Desktop/DRA BRAINS';
const userId = '{{USER_ID}}';

// Supabase config
const SUPABASE_URL = '{{SUPABASE_URL}}';
const SUPABASE_KEY = process.env.SUPABASE_SERVICE_ROLE_KEY || '';

function genId() { return 'mem-' + Math.random().toString(36).slice(2, 11); }

// -- Safe store loading (never nukes memories) --
let store = {
  state: {
    memories: [],
    relations: [],
    projects: ['milo'],
    activeProject: 'milo',
    currentUser: { id: userId, email: 'milo@agent.local', name: 'Milo' }
  },
  version: 0
};

if (fs.existsSync(storeFile)) {
  try {
    const raw = fs.readFileSync(storeFile, 'utf-8');
    const parsed = JSON.parse(raw);
    if (parsed.state && Array.isArray(parsed.state.memories)) {
      store = parsed;
    } else {
      throw new Error('Invalid store structure');
    }
  } catch (e) {
    console.error('Store parse failed:', e.message);
    if (fs.existsSync(storeBackup)) {
      try {
        const raw = fs.readFileSync(storeBackup, 'utf-8');
        const parsed = JSON.parse(raw);
        if (parsed.state && Array.isArray(parsed.state.memories)) {
          store = parsed;
          console.error('Restored from backup');
        }
      } catch (e2) {
        console.error('Backup also corrupt, starting fresh');
      }
    }
  }
}

// Backup before any write
if (fs.existsSync(storeFile)) {
  try { fs.copyFileSync(storeFile, storeBackup); } catch (e) { /* skip */ }
}

const memoriesBefore = store.state.memories.length;
const newMemories = [];

function addMemory(content, category, tags, importance) {
  const now = new Date().toISOString();
  if (store.state.memories.some(m => m.content === content)) return null;
  const mem = {
    id: genId(), content, category,
    project_name: store.state.activeProject || 'milo',
    source: 'manual', tags: tags || [], importance: importance || 3,
    user_id: userId, created_at: now, updated_at: now, embedding: null,
  };
  store.state.memories.push(mem);
  newMemories.push(mem);
  return mem;
}

// CLI: node persist-memories.cjs "content" "category" "tag1,tag2" importance
const args = process.argv.slice(2);
if (args.length >= 2) {
  addMemory(args[0], args[1], args[2] ? args[2].split(',') : [], parseInt(args[3]) || 3);
}

// Sync latest daily notes to memories
const dailyNotesDir = path.join(vaultDir, '01 - Daily Notes');
if (fs.existsSync(dailyNotesDir)) {
  const files = fs.readdirSync(dailyNotesDir)
    .filter(f => f.endsWith('.md') && /^\d{4}-\d{2}-\d{2}/.test(f))
    .sort()
    .slice(-5);
  for (const file of files) {
    const dateStr = file.replace('.md', '');
    addMemory(`Daily note ${dateStr}: session log exists in vault`, 'context', ['vault', 'daily-note', dateStr], 2);
  }
}

// -- Write to local store --
const out = JSON.stringify(store, null, 2);
fs.writeFileSync(storeFile, out, 'utf-8');
const addedLocal = store.state.memories.length - memoriesBefore;
console.log(`Local: added ${addedLocal} memories. Total: ${store.state.memories.length}`);

// -- Sync new memories to Supabase --
if (SUPABASE_KEY && newMemories.length > 0) {
  let synced = 0;
  let errors = 0;
  let done = 0;
  const total = newMemories.length;

  for (const mem of newMemories) {
    const body = JSON.stringify({
      id: mem.id,
      user_id: userId,
      project_name: mem.project_name,
      content: mem.content,
      category: mem.category,
      importance: mem.importance,
      tags: mem.tags,
      source: 'manual',
      strength: 1,
      access_count: 0,
      is_latest: true,
      created_at: mem.created_at,
      updated_at: mem.updated_at,
    });

    const req = https.request(`${SUPABASE_URL}/rest/v1/memories`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'apikey': SUPABASE_KEY,
        'Authorization': `Bearer ${SUPABASE_KEY}`,
        'Prefer': 'return=minimal',
      },
    }, (res) => {
      if (res.statusCode >= 200 && res.statusCode < 300) synced++;
      else errors++;
      done++;
      if (done === total) {
        console.log(`Supabase: synced ${synced}/${total} (${errors} errors)`);
        process.exit(0);
      }
      res.resume();
    });
    req.on('error', () => { errors++; done++; if (done === total) { console.log(`Supabase: synced ${synced}/${total} (${errors} errors)`); process.exit(0); } });
    req.write(body);
    req.end();
  }
} else {
  if (newMemories.length > 0) console.log('Supabase: skipped (no key or no new memories)');
  console.log('Done');
}
