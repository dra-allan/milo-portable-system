const { spawn } = require('child_process');
const path = require('path');
const fs = require('fs');

const REPO_ROOT = path.resolve(process.env.MILO_REPO_ROOT || path.join(__dirname, '..'));
const MILO_HOME = path.resolve(process.env.MILO_HOME || path.join(process.env.LOCALAPPDATA || process.env.HOME || process.cwd(), '.milo'));
const LOG_DIR = path.join(MILO_HOME, 'logs');
fs.mkdirSync(LOG_DIR, { recursive: true });

function log(message) {
  const line = `[${new Date().toISOString()}] ${message}\n`;
  fs.appendFileSync(path.join(LOG_DIR, 'launcher.log'), line);
  console.log(`Milo: ${message}`);
}

function start(name, command, args) {
  const child = spawn(command, args, {
    cwd: REPO_ROOT,
    detached: true,
    stdio: 'ignore',
    env: { ...process.env, MILO_HOME, MILO_REPO_ROOT: REPO_ROOT }
  });
  child.on('error', err => log(`${name} failed: ${err.message}`));
  child.unref();
  log(`${name} started (pid ${child.pid})`);
}

function startMiloServices() {
  log(`Starting services from ${REPO_ROOT}`);
  const bot = path.join(REPO_ROOT, 'milo-bot', 'src', 'bot.py');
  if (fs.existsSync(bot)) start('Telegram bot', process.platform === 'win32' ? 'python' : 'python3', [bot]);
  else log(`Telegram bot not found: ${bot}`);
  const watcher = path.join(REPO_ROOT, 'scripts', 'git-watcher.js');
  if (fs.existsSync(watcher)) start('Git sync watcher', process.execPath, [watcher]);
  else log(`Git sync watcher not found: ${watcher}`);
}

if (require.main === module) startMiloServices();
module.exports = { startMiloServices };
