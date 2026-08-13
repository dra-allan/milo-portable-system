#!/usr/bin/env node
'use strict';
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
const https = require('https');

const home = path.resolve(process.env.MILO_HOME || path.join(process.env.LOCALAPPDATA || process.env.HOME || process.cwd(), '.milo'));
const storageDir = path.resolve(process.env.AGENTMEMORY_HOME || path.join(home, 'storage'));
const storeFile = path.join(storageDir, 'agent-memory-store.json');
const backupFile = `${storeFile}.bak`;
const vaultDir = path.resolve(process.env.MILO_VAULT_DIR || path.join(process.env.HOME || home, 'vault'));
const userId = process.env.MILO_USER_ID || 'local-user';
const supabaseUrl = (process.env.SUPABASE_URL || '').replace(/\/$/, '');
const supabaseKey = process.env.SUPABASE_SERVICE_ROLE_KEY || '';

const emptyStore = () => ({ state: { memories: [], relations: [], projects: ['milo'], activeProject: 'milo', currentUser: { id: userId, name: process.env.MILO_USER_NAME || 'Milo' } }, version: 0 });
function readStore() {
  for (const file of [storeFile, backupFile]) {
    try {
      if (!fs.existsSync(file)) continue;
      const parsed = JSON.parse(fs.readFileSync(file, 'utf8'));
      if (parsed.state && Array.isArray(parsed.state.memories)) return parsed;
    } catch (_) { /* try backup */ }
  }
  return emptyStore();
}
function atomicWrite(file, content) {
  const tmp = `${file}.${process.pid}.tmp`;
  fs.writeFileSync(tmp, content, { encoding: 'utf8', mode: 0o600 });
  fs.renameSync(tmp, file);
}
function addMemory(store, content, category, tags, importance) {
  if (!content || store.state.memories.some(m => m.content === content)) return null;
  const now = new Date().toISOString();
  const mem = { id: `mem-${crypto.randomUUID()}`, content, category: category || 'fact', project_name: store.state.activeProject || 'milo', source: 'manual', tags: tags || [], importance: Number.isFinite(importance) ? importance : 3, user_id: userId, created_at: now, updated_at: now, embedding: null };
  store.state.memories.push(mem); return mem;
}
async function syncMemory(mem) {
  if (!supabaseUrl || !supabaseKey) return false;
  let url; try { url = new URL(`${supabaseUrl}/rest/v1/memories`); } catch (_) { return false; }
  return new Promise(resolve => {
    const body = JSON.stringify({ ...mem, project_name: mem.project_name, strength: 1, access_count: 0, is_latest: true });
    const req = https.request(url, { method: 'POST', headers: { 'Content-Type': 'application/json', apikey: supabaseKey, Authorization: `Bearer ${supabaseKey}`, Prefer: 'return=minimal' } }, res => { res.resume(); resolve(res.statusCode >= 200 && res.statusCode < 300); });
    req.on('error', () => resolve(false)); req.setTimeout(15000, () => { req.destroy(); resolve(false); }); req.end(body);
  });
}
(async () => {
  fs.mkdirSync(storageDir, { recursive: true, mode: 0o700 });
  const store = readStore();
  if (fs.existsSync(storeFile)) { try { fs.copyFileSync(storeFile, backupFile); } catch (_) {} }
  const args = process.argv.slice(2), added = [];
  if (args.length >= 2) { const mem = addMemory(store, args[0], args[1], args[2] ? args[2].split(',').map(x => x.trim()).filter(Boolean) : [], Number.parseInt(args[3], 10) || 3); if (mem) added.push(mem); }
  const daily = path.join(vaultDir, '01 - Daily Notes');
  if (fs.existsSync(daily)) for (const file of fs.readdirSync(daily).filter(f => /^\d{4}-\d{2}-\d{2}.*\.md$/.test(f)).sort().slice(-5)) { const mem = addMemory(store, `Daily note ${file}: session log exists in vault`, 'context', ['vault', 'daily-note'], 2); if (mem) added.push(mem); }
  atomicWrite(storeFile, JSON.stringify(store, null, 2));
  let synced = 0; for (const mem of added) if (await syncMemory(mem)) synced++;
  console.log(`Local: added ${added.length} memories. Supabase: synced ${synced}/${added.length}`);
})().catch(err => { console.error(`Persistence failed: ${err.message}`); process.exitCode = 1; });
