const { spawn } = require('child_process');
const path = require('path');
const fs = require('fs');

const MILO_HOME = process.env.MILO_HOME || path.resolve('./MILO_HOME');
const LOG_DIR = path.join(MILO_HOME, 'logs');
const TELEGRAM_HOME = path.join(MILO_HOME, 'telegram-data');

// Ensure directories exist
[LOG_DIR, path.join(MILO_HOME, 'data', 'backups')].forEach(dir => 
  fs.mkdirSync(dir, { recursive: true })
);

function log(message) {
  const timestamp = new Date().toISOString();
  const logLine = `[${timestamp}] ${message}\n`;
  fs.appendFileSync(path.join(LOG_DIR, 'launcher.log'), logLine, { flag: 'a' });
  console.log(`🚀 ${message}`);
}

function startService(name, command, args, options = {}) {
  try {
    const ps = spawn(command, args, {
      detached: true,
      stdio: 'ignore',
      cwd: MILO_HOME,
      env: {
        ...process.env,
        MILO_HOME,
        // For Telegram bot, we need to point to our telegram-data
        ...(name.includes('Telegram') && { 
          OPENCODE_TELEGRAM_HOME: TELEGRAM_HOME,
          // Also set the MILO_DB_PATH to our portable database
          MILO_DB_PATH: path.join(MILO_HOME, 'data', 'memories.db')
        })
      },
      ...options.env
    });
    
    ps.unref(); // Prevent Node.js from waiting for exit
    log(`✅ ${name} started (PID: ${ps.pid})${options.silent ? '' : ' - Check logs for details'}`);
    return ps;
  } catch (error) {
    log(`❌ Failed to start ${name}: ${error.message}`);
    return null;
  }
}

function startMiloServices() {
  log('🚀 Starting Milo Portable Services...');
  
  // 1. Start Telegram bot (using the existing bot.py)
  const botScript = path.join(MILO_HOME, 'milo-bot', 'src', 'bot.py');
  if (fs.existsSync(botScript)) {
    startService(
      'MiloTelegramBot', 
      process.platform === 'win32' ? 'python' : 'python3', 
      [botScript],
      { 
        silent: true 
      }
    );
    
    // Also capture bot output for logging
    const botPs = spawn(process.platform === 'win32' ? 'python' : 'python3', [botScript], {
      cwd: MILO_HOME,
      env: {
        ...process.env,
        MILO_HOME,
        OPENCODE_TELEGRAM_HOME: TELEGRAM_HOME,
        MILO_DB_PATH: path.join(MILO_HOME, 'data', 'memories.db')
      }
    });
    
    botPs.stdout.on('data', data => {
      const msg = data.toString().trim();
      if (msg) log(`[Telegram] ${msg}`);
    });
    botPs.stderr.on('data', data => {
      const msg = data.toString().trim();
      if (msg) log(`[Telegram ERROR] ${msg}`);
    });
    botPs.unref();
  } else {
    log(`❌ Telegram bot script not found: ${botScript}`);
  }

  // 2. Start GitHub bidirectional sync watcher
  const watcherPath = path.join(MILO_HOME, 'scripts', 'git-watcher.js');
  if (fs.existsSync(watcherPath)) {
    startService(
      'GitSyncWatcher',
      'node',
      [watcherPath]
    );
  } else {
    log(`⚠️ Git sync watcher not found: ${watcherPath}`);
    log('💡 Deploy git-watcher.js to enable bidirectional GitHub sync');
  }

  log('✅ Milo Portable services initiated');
  log('💡 Verification:');
  log(`   - Telegram bot: Message @Milo_drabot`);
  log(`   - GitHub sync: ${path.join(MILO_HOME, 'logs', 'launcher.log')}`);
}

// Start services if run directly
if (require.main === module) {
  startMiloServices();
}

module.exports = { startMiloServices };
