// Milo MCP Bundle Server
// Provides memory access and sync status via MCP for both OpenCode and Claude Code
const { Server } = require('@modelcontextprotocol/sdk/server');
const { StdioServerTransport } = require('@modelcontextprotocol/sdk/server/stdio');
const {
  ListResourcesTemplate,
  ReadResourceTemplate
} = require('@modelcontextprotocol/sdk/server/resources');
const {
  ListToolsTemplate,
  CallToolTemplate
} = require('@modelcontextprotocol/sdk/server/tools');
const fs = require('fs');
const path = require('path');
const sqlite3 = require('sqlite3').verbose();
const { execSync } = require('child_process');

const MILO_HOME = process.env.MILO_HOME || path.resolve('./MILO_HOME');

class MiloMCPServer {
  constructor() {
    this.server = new Server(
      {
        name: "milo-portable",
        version: "1.0.0"
      },
      {
        capabilities: {
          resources: {},
          tools: {}
        }
      }
    );

    this.db = new sqlite3.Database(
      path.join(MILO_HOME, 'data', 'memories.db'),
      sqlite3.OPEN_READWRITE | sqlite3.OPEN_CREATE,
      (err) => { if (err) console.error('DB connection error:', err); }
    );

    this.initializeDB();
    this.setupHandlers();
  }

  initializeDB() {
    this.db.run(`
      CREATE TABLE IF NOT EXISTS memories (
        id TEXT PRIMARY KEY,
        title TEXT NOT NULL,
        type TEXT NOT NULL,
        content TEXT NOT NULL,
        timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
        scope TEXT DEFAULT 'personal'
      )
    `);
  }

  setupHandlers() {
    // Memory search tool
    this.server.setRequestHandler(ListToolsTemplate, async () => ({
      tools: [
        {
          name: "milo_search_memories",
          description: "Search Milo's persistent memory store",
          inputSchema: {
            type: "object",
            properties: {
              query: {
                type: "string",
                description: "Search term to find in memories"
              },
              limit: {
                type: "integer",
                description: "Maximum results to return (default 10)",
                default: 10
              }
            },
            required: ["query"]
          }
        }
      ]
    }));

    this.server.setRequestHandler(CallToolTemplate, async (request) => {
      if (request.params.name === "milo_search_memories") {
        const { query, limit } = request.params.arguments;

        return new Promise((resolve, reject) => {
          this.db.all(
            `SELECT * FROM memories
             WHERE title LIKE ? OR content LIKE ?
             ORDER BY timestamp DESC
             LIMIT ?`,
            [`%${query}%`, `%${query}%`, limit],
            (err, rows) => {
              if (err) {
                reject(new Error(`Database error: ${err.message}`));
                return;
              }

              resolve({
                content: [{
                  type: "text",
                  text: JSON.stringify({
                    count: rows.length,
                    memories: rows.map(m => ({
                      id: m.id,
                      title: m.title,
                      type: m.type,
                      content: m.content,
                      timestamp: m.timestamp
                    }))
                  }, null, 2)
                }]
              });
            }
          );
        });
      }

      throw new Error(`Unknown tool: ${request.params.name}`);
    });

    // Resource: Sync status
    this.server.setRequestHandler(ListResourcesTemplate, async () => ({
      resources: [
        {
          uri: "milo://sync/status",
          name: "Sync Status",
          description: "Current GitHub sync state and instance awareness",
          mimeType: "application/json"
        }
      ]
    }));

    this.server.setRequestHandler(ReadResourceTemplate, async (request) => {
      if (request.params.uri === "milo://sync/status") {
        return new Promise((resolve, reject) => {
          try {
            const { stdout } = execSync(
              'git notes show --ref=refs/notes/milo-sync HEAD',
              { encoding: 'utf8', stdio: 'pipe' }
            ).catch(() => '[]');

            const notes = JSON.parse(stdout);
            const lastSync = notes[notes.length - 1] || null;

            const instances = {};
            notes.forEach(note => {
              if (note.instance) {
                if (!instances[note.instance]) {
                  instances[note.instance] = [];
                }
                instances[note.instance].push(note.timestamp);
              }
            });

            resolve({
              contents: [{
                uri: "milo://sync/status",
                mimeType: "application/json",
                text: JSON.stringify({
                  last_sync: lastSync,
                  instance_activity: Object.fromEntries(
                    Object.entries(instances).map(([id, timestamps]) => [
                      id,
                      {
                        last_seen: timestamps[timestamps.length - 1] || null,
                        sync_count: timestamps.length
                      }
                    ])
                  ),
                  has_local_changes: execSync('git status --porcelain', { encoding: 'utf8' }).trim().length > 0,
                  sync_active: true,
                  repo_url: execSync('git config --get remote.origin.url', { encoding: 'utf8' }).trim()
                }, null, 2)
              }]
            });
          } catch (error) {
            reject(new Error(`Failed to get sync status: ${error.message}`));
          }
        });
      }

      throw new Error(`Unknown resource: ${request.params.uri}`);
    });
  }

  async start() {
    const transport = new StdioServerTransport();
    await this.server.connect(transport);
    console.log('🔌 Milo Portable MCP Server started');

    // Handle shutdown
    process.on('SIGINT', async () => {
      await this.server.close();
      this.db.close();
      process.exit(0);
    });
  }
}

// Start if executed directly
if (require.main === module) {
  const server = new MiloMCPServer();
  server.start().catch(console.error);
}

module.exports = { MiloMCPServer };