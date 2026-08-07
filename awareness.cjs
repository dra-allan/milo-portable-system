#!/usr/bin/env node
const fs = require('fs');
const path = require('path');

const AWARENESS_PATH = path.join(__dirname, 'awareness.json');

function read() {
  try {
    return JSON.parse(fs.readFileSync(AWARENESS_PATH, 'utf-8'));
  } catch {
    return { sessions: {} };
  }
}

function write(data) {
  fs.writeFileSync(AWARENESS_PATH, JSON.stringify(data, null, 2));
}

function getSessionId() {
  return process.env.MILO_SESSION_NAME || `session-${require('os').hostname()}-${Date.now()}`;
}

const [, , command, ...args] = process.argv;

switch (command) {
  case 'start': {
    const data = read();
    const id = getSessionId();
    const task = args.join(' ') || 'unspecified';
    data.sessions[id] = {
      id,
      name: process.env.MILO_SESSION_NAME || id,
      task,
      status: 'in_progress',
      startedAt: new Date().toISOString(),
      lastHeartbeat: new Date().toISOString()
    };
    write(data);
    console.log(JSON.stringify(data.sessions[id]));
    break;
  }
  case 'heartbeat': {
    const data = read();
    const id = getSessionId();
    if (data.sessions[id]) {
      data.sessions[id].lastHeartbeat = new Date().toISOString();
      write(data);
    }
    break;
  }
  case 'done': {
    const data = read();
    const id = getSessionId();
    if (data.sessions[id]) {
      data.sessions[id].status = 'done';
      data.sessions[id].completedAt = new Date().toISOString();
      write(data);
      console.log(JSON.stringify(data.sessions[id]));
    }
    break;
  }
  case 'list': {
    const data = read();
    const active = Object.values(data.sessions)
      .filter(s => s.status === 'in_progress')
      .sort((a, b) => new Date(b.lastHeartbeat) - new Date(a.lastHeartbeat));
    console.log(JSON.stringify(active));
    break;
  }
  case 'status': {
    const data = read();
    const id = getSessionId();
    if (data.sessions[id]) {
      data.sessions[id].task = args.join(' ') || data.sessions[id].task;
      data.sessions[id].lastHeartbeat = new Date().toISOString();
      write(data);
      console.log(JSON.stringify(data.sessions[id]));
    }
    break;
  }
  case 'cleanup': {
    const data = read();
    const cutoff = Date.now() - 1000 * 60 * 30; // 30 min stale
    for (const [id, session] of Object.entries(data.sessions)) {
      if (session.status === 'done' || new Date(session.lastHeartbeat).getTime() < cutoff) {
        delete data.sessions[id];
      }
    }
    write(data);
    console.log('cleanup done');
    break;
  }
  default:
    console.log(`Usage: node awareness.cjs <start|heartbeat|done|list|status|cleanup> [task description]`);
    console.log(`  Set MILO_SESSION_NAME env var to name this session.`);
}
