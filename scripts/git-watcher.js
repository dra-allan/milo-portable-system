// Bidirectional GitHub sync watcher for Milo Portable
// Pushes local changes → pulls remote changes → handles conflicts
const chokidar = require('chokidar');
const { execSync, spawn } = require('child_process');
const fs = require('fs');
const path = require('path');

const MILO_HOME = process.env.MILO_HOME || path.resolve('./MILO_HOME');
const GIT_REPO_PATH = MILO_HOME;
const LOG_FILE = path.join(MILO_HOME, 'data', 'backups', 'git-sync.log');
const WATCH_PATHS = [
  path.join(MILO_HOME, 'vault'),
  path.join(MILO_HOME, 'data', 'memories.db'),
  path.join(MILO_HOME, 'Session Handoff.md'),
  path.join(MILO_HOME, 'Active Priorities.md')
];
const SYNC_INTERVAL_MS = 30000; // 30 seconds debounce
const PULL_TIMEOUT_MS = 10000;  // 10 second pull timeout

fs.mkdirSync(path.join(MILO_HOME, 'data', 'backups'), { recursive: true });

function log(message, level = 'INFO') {
  const timestamp = new Date().toISOString();
  const logLine = `[${timestamp}] [${level}] ${message}\n`;
  fs.appendFileSync(LOG_FILE, logLine, { flag: 'a' });
  console.log(`🔄 ${message}`);
}

// Execute git command with timeout
function gitCommand(args, options = {}) {
  return new Promise((resolve, reject) => {
    const timeout = setTimeout(() => {
      reject(new Error(`Git command timed out after ${options.timeout || PULL_TIMEOUT_MS}ms`));
    }, options.timeout || PULL_TIMEOUT_MS);

    const child = spawn('git', args, {
      cwd: GIT_REPO_PATH,
      ...options
    });

    let stdout = '';
    let stderr = '';

    child.stdout.on('data', (data) => { stdout += data.toString(); });
    child.stderr.on('data', (data) => { stderr += data.toString(); });

    child.on('close', (code) => {
      clearTimeout(timeout);
      if (code === 0) resolve({ stdout, stderr });
      else reject(new Error(`Git failed (${code}): ${stderr}`));
    });
  });
}

// Check if we have local changes (ignoring untracked files we might ignore)
function hasLocalChanges() {
  return gitCommand(['diff-index', '--quiet', 'HEAD', '--']).then(
    () => false, // No changes
  ', 'HEAD', '--']).then(
    () => false, // No changes
    () => true   // Has changes
  ).catch(() => true); // Assume changes on error
}

// Check if remote has changes we don't have
function remoteHasChanges() {
  return gitCommand(['fetch', 'origin']).then(() =>
    gitCommand(['diff', '--quiet', 'HEAD', 'origin/@{upstream}'])
  ).then(
    () => false, // No remote changes
    () => true   // Has remote changes
  ).catch(() => false); // Assume no changes on fetch error
}

// Perform safe pull (fast-forward only when possible)
function safePull() {
  return gitCommand(['pull', '--ff-only']).then(
    () => ({ type: 'fast-forward', message: 'Fast-forward pull successful' }),
    (err) => {
      // If ff-only fails, try regular pull (may create merge commit)
      if (err.message.includes('not possible')) {
        return gitCommand(['pull']).then(
          () => ({ type: 'merge', message: 'Merge pull successful' }),
          (mergeErr) => {
            // Check if merge created conflicts
            return gitCommand(['diff', '--name-only', '--diff-filter=U']).then(
              (conflictCheck) => {
                if (conflictCheck.stdout.trim()) {
                  throw new Error(`Merge conflicts in files: ${conflictCheck.stdout.trim()}`);
                }
                return { type: 'merge-conflict-free', message: 'Merge pull successful (no conflicts)' };
              }
            );
          }
        );
      }
      throw err; // Re-throw original ff-only error
    }
  );
}

// Perform push with upstream tracking
function pushChanges() {
  return gitCommand(['push', '-u', 'origin', 'HEAD']).then(
    () => ({ message: 'Push successful' }),
    (err) => {
      // Handle common push errors
      if (err.message.includes('non-fast-forward')) {
        throw new Error('Push rejected: Remote has new changes. Pull first.');
      }
      throw err;
    }
  );
}

// Main sync cycle: Push local → Pull remote
async function syncCycle() {
  try {
    log('🔄 Starting sync cycle...');

    // Step 1: Check if we have local changes to push
    const hasLocal = await hasLocalChanges();
    if (!hasLocal) {
      log('📭 No local changes to push');
    } else {
      log('📤 Local changes detected, pushing to GitHub...');
      const pushResult = await pushChanges();
      log(pushResult.message);
    }

    // Step 2: Check if remote has changes we need
    const hasRemote = await remoteHasChanges();
    if (!hasRemote) {
      log('📥 No remote changes to pull');
      return; // Early exit if no remote changes
    }

    log('📥 Remote changes detected, pulling from GitHub...');
    const pullResult = await safePull();
    log(pullResult.message);

    // Log what changed during pull (for awareness)
    const changedFiles = await gitCommand(['diff', '--name-only', 'HEAD@{1}', 'HEAD']);
    if (changedFiles.stdout.trim()) {
      log(`📝 Files updated: ${changedFiles.stdout.trim().split('\n').filter(Boolean).join(', ')}`);
    }

    // Update last sync timestamp for awareness via Git notes
    await gitCommand(['notes', '--ref=refs/notes/milo-sync', 'add', '-m',
      JSON.stringify({
        timestamp: new Date().toISOString(),
        instance: process.env.MILO_INSTANCE_ID || 'unknown',
        action: 'sync'
      }), 'HEAD']);

    log('✅ Sync cycle complete');

  } catch (error) {
    // Handle specific error types
    if (error.message.includes('Merge conflicts')) {
      log(`⚠️ MERGE CONFLICT DETECTED: ${error.message}`, 'WARN');
      log('💡 Resolve manually: Edit conflicted files, then `git add <file>` and `git commit`', 'WARN');
    } else if (error.message.includes('Push rejected')) {
      log(`⚠️ PUSH REJECTED: ${error.message}`, 'WARN');
      log('💡 Remote has newer changes. Next sync cycle will pull first.', 'WARN');
    } else if (error.message.includes('timed out')) {
      log(`⏰ SYNC TIMEOUT: ${error.message}`, 'ERROR');
      log('💡 Check network connection or GitHub status', 'ERROR');
    } else {
      log(`❌ SYNC ERROR: ${error.message}`, 'ERROR');
    }

    // Don't throw - we want the watcher to keep running
  }
}

// File watcher with debounce
function startFileWatcher() {
  log('👀 Starting file watcher for bidirectional sync...');

  const watcher = chokidar.watch(WATCH_PATHS, {
    persistent: true,
    ignoreInitial: true,
    awaitWriteFinish: {
      stabilityThreshold: 500, // Wait 0.5s after last change
      pollInterval: 100
    }
  });

  let debounceTimer = null;

  function scheduleSync() {
    clearTimeout(debounceTimer);
    debounceTimer = setTimeout(() => {
      log('⏳ Debounce period ended, initiating sync...');
      syncCycle();
    }, SYNC_INTERVAL_MS);
  }

  watcher
    .on('add', (path) => {
      log(`📄 File added: ${path.replace(MILO_HOME, '.')}`);
      scheduleSync();
    })
    .on('change', (path) => {
      log(`📝 File changed: ${path.replace(MILO_HOME, '.')}`);
      scheduleSync();
    })
    .on('unlink', (path) => {
      log(`🗑️ File removed: ${path.replace(MILO_HOME, '.')}`);
      scheduleSync();
    })
    .on('error', (error) => {
      log(`⚠️ Watcher error: ${error.message}`, 'ERROR');
    });

  // Initial sync on startup
  setTimeout(() => {
    log('🚀 Performing initial sync...');
    syncCycle();
  }, 5000);

  return {
    close: () => watcher.close()
  };
}

// Graceful shutdown
function shutdown(watcher) {
  log('🛑 Shutting down sync watcher...');
  watcher.close();
  // Try one final sync on exit
  syncCycle().then(() => process.exit(0)).catch(() => process.exit(1));
}

// Start if run directly
if (require.main === module) {
  const watcher = startFileWatcher();

  process.on('SIGINT', () => shutdown(watcher));
  process.on('SIGTERM', () => shutdown(watcher));

  log('✅ Bidirectional GitHub sync watcher active');
  log('🔄 Will push local changes → pull remote changes every 30s of silence');
  log('⚠️ Conflicts will be logged for manual resolution');
}

module.exports = { start: startFileWatcher };