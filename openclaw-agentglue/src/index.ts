/**
 * OpenClaw AgentGlue Plugin v0.3.0
 *
 * Cross-process dedup cache for multi-agent tool calls.
 *
 * Architecture:
 *  - after_tool_call hook auto-caches ALL read-only tool results to SQLite via sidecar
 *  - Smart proxy tools (agentglue_read, agentglue_search, etc.) check cache first
 *  - Single sidecar process shared by all agents/sub-agents
 */

import * as http from 'http';
import { spawn, ChildProcess } from 'child_process';
import * as path from 'path';

// Tools that should never be cached (side-effectful)
const SKIP_TOOLS = new Set([
  'edit', 'write', 'bash', 'exec', 'send_message', 'deliver',
  'send_telegram', 'send_whatsapp', 'send_discord',
  'notebook_edit', 'task_create', 'task_update',
  'agentglue_metrics', 'agentglue_health',
  // Also skip caching our own proxy tools to avoid recursive caching
  'agentglue_cached_read', 'agentglue_cached_search', 'agentglue_cached_list',
]);

interface SidecarConfig {
  host: string;
  port: number;
  autoStart: boolean;
  maxRestarts: number;
  restartDelayMs: number;
  healthCheckIntervalMs: number;
  cacheTTL: number;
  dbPath: string;
}

// ---------------------------------------------------------------------------
// Sidecar HTTP client
// ---------------------------------------------------------------------------

class SidecarClient {
  constructor(private host: string, private port: number) {}

  async healthCheck(): Promise<boolean> {
    return new Promise((resolve) => {
      const req = http.get(
        `http://${this.host}:${this.port}/health`,
        { timeout: 5000 },
        (res) => {
          let data = '';
          res.on('data', (chunk) => { data += chunk; });
          res.on('end', () => {
            try { resolve(JSON.parse(data).status === 'ok'); }
            catch { resolve(false); }
          });
        }
      );
      req.on('error', () => resolve(false));
      req.on('timeout', () => { req.destroy(); resolve(false); });
    });
  }

  private async post(path: string, body: Record<string, unknown>): Promise<any> {
    return new Promise((resolve, reject) => {
      const postData = JSON.stringify(body);
      const options = {
        hostname: this.host,
        port: this.port,
        path,
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Content-Length': Buffer.byteLength(postData),
        },
        timeout: 30000,
      };
      const req = http.request(options, (res) => {
        let data = '';
        res.on('data', (chunk) => { data += chunk; });
        res.on('end', () => {
          try {
            const json = JSON.parse(data);
            if (json.error) reject(new Error(json.error));
            else resolve(json);
          } catch (e) {
            reject(new Error(`Invalid JSON: ${data.slice(0, 200)}`));
          }
        });
      });
      req.on('error', (err) => reject(new Error(`Sidecar: ${err.message}`)));
      req.on('timeout', () => { req.destroy(); reject(new Error('Sidecar timeout')); });
      req.write(postData);
      req.end();
    });
  }

  async call(tool: string, params: Record<string, unknown>): Promise<string> {
    const res = await this.post('/call', { tool, params });
    return res.result;
  }

  async cacheCheck(tool: string, params: Record<string, unknown>): Promise<{ hit: boolean; result?: any; age_s?: number }> {
    return this.post('/cache/check', { tool, params });
  }

  async cacheStore(tool: string, params: Record<string, unknown>, result: unknown, ttl: number, agentId?: string): Promise<void> {
    await this.post('/cache/store', { tool, params, result, ttl, agent_id: agentId || '' });
  }

  async cacheStats(): Promise<Record<string, unknown>> {
    return this.post('/cache/stats', {});
  }
}

// ---------------------------------------------------------------------------
// Sidecar lifecycle manager
// ---------------------------------------------------------------------------

class SidecarManager {
  private process: ChildProcess | null = null;
  private restartCount = 0;
  private healthTimer: NodeJS.Timeout | null = null;
  private shuttingDown = false;

  constructor(
    private config: SidecarConfig,
    private pluginDir: string,
    private client: SidecarClient,
    private log: (msg: string) => void,
  ) {}

  async start(): Promise<void> {
    if (this.shuttingDown) return;
    if (this.process) return;

    const alreadyRunning = await this.client.healthCheck();
    if (alreadyRunning) {
      this.log('Sidecar already running (external)');
      this.startHealthMonitor();
      return;
    }

    const sidecarPath = path.join(this.pluginDir, 'sidecar', 'server.py');
    this.log(`Starting sidecar: python3 ${sidecarPath} --host ${this.config.host} --port ${this.config.port}`);

    const args = [sidecarPath, '--host', this.config.host, '--port', String(this.config.port)];
    if (this.config.dbPath) {
      args.push('--db-path', this.config.dbPath);
    }

    return new Promise((resolve, reject) => {
      this.process = spawn('python3', args, {
        cwd: this.pluginDir,
        detached: false,
        stdio: ['ignore', 'pipe', 'pipe'],
      });

      this.process.stdout?.on('data', (d) => this.log(d.toString().trim()));
      this.process.stderr?.on('data', (d) => this.log(`[err] ${d.toString().trim()}`));

      this.process.on('error', (err) => {
        this.process = null;
        reject(err);
      });

      this.process.on('exit', (code, signal) => {
        this.log(`Sidecar exited (code=${code}, signal=${signal})`);
        this.process = null;
        if (!this.shuttingDown && this.config.autoStart && this.restartCount < this.config.maxRestarts) {
          this.restartCount++;
          this.log(`Restarting sidecar (${this.restartCount}/${this.config.maxRestarts})...`);
          setTimeout(() => this.start().catch((e) => this.log(`Restart failed: ${e.message}`)), this.config.restartDelayMs);
        }
      });

      // Wait for health
      const check = async () => {
        for (let i = 0; i < 30; i++) {
          await new Promise((r) => setTimeout(r, 500));
          if (await this.client.healthCheck()) {
            this.restartCount = 0;
            this.startHealthMonitor();
            resolve();
            return;
          }
        }
        reject(new Error('Sidecar failed to become healthy within 15s'));
      };
      check();
    });
  }

  async stop(): Promise<void> {
    this.shuttingDown = true;
    this.stopHealthMonitor();
    if (!this.process) return;
    this.log('Stopping sidecar...');
    return new Promise((resolve) => {
      const timeout = setTimeout(() => { this.process?.kill('SIGKILL'); resolve(); }, 5000);
      this.process?.once('exit', () => { clearTimeout(timeout); this.process = null; resolve(); });
      this.process?.kill('SIGTERM');
    });
  }

  private startHealthMonitor(): void {
    if (this.healthTimer) return;
    this.healthTimer = setInterval(async () => {
      const ok = await this.client.healthCheck();
      if (!ok && !this.shuttingDown) this.log('Health check failed');
    }, this.config.healthCheckIntervalMs);
  }

  private stopHealthMonitor(): void {
    if (this.healthTimer) { clearInterval(this.healthTimer); this.healthTimer = null; }
  }
}

// ---------------------------------------------------------------------------
// OpenClaw Plugin (new SDK format)
// ---------------------------------------------------------------------------

const DEFAULT_CONFIG: SidecarConfig = {
  host: '127.0.0.1',
  port: 8765,
  autoStart: true,
  maxRestarts: 3,
  restartDelayMs: 2000,
  healthCheckIntervalMs: 30000,
  cacheTTL: 300,
  dbPath: '',
};

// For backward compatibility: also export the class-based interface
class AgentGluePlugin {
  private config: SidecarConfig;
  private client: SidecarClient;
  private manager: SidecarManager | null = null;

  constructor() {
    this.config = { ...DEFAULT_CONFIG };
    this.client = new SidecarClient(this.config.host, this.config.port);
  }

  async init(context: { pluginDir: string; config?: Partial<SidecarConfig> }): Promise<void> {
    if (context.config) this.config = { ...this.config, ...context.config };
    this.client = new SidecarClient(this.config.host, this.config.port);
    this.manager = new SidecarManager(this.config, context.pluginDir, this.client, (m) => console.log(`[AgentGlue] ${m}`));
    if (this.config.autoStart) await this.manager.start();
  }

  async shutdown(): Promise<void> {
    await this.manager?.stop();
  }

  // Proxy methods
  async agentglue_search(params: { query: string }) { return this.client.call('search', params); }
  async agentglue_metrics() { return JSON.stringify(await this.client.cacheStats(), null, 2); }
  async deduped_search(params: Record<string, unknown>) { return this.client.call('deduped_search', params); }
  async deduped_read_file(params: Record<string, unknown>) { return this.client.call('deduped_read_file', params); }
  async deduped_list_files(params: Record<string, unknown>) { return this.client.call('deduped_list_files', params); }
  async agentglue_health() { const ok = await this.client.healthCheck(); return JSON.stringify({ healthy: ok, backend: 'sqlite', host: this.config.host, port: this.config.port, dbPath: this.config.dbPath || '~/.openclaw/cache/agentglue.db' }); }
}

// ---------------------------------------------------------------------------
// New Plugin SDK registration (OpenClaw 2026.3+)
// ---------------------------------------------------------------------------

interface PluginApi {
  id: string;
  name: string;
  config: Record<string, unknown>;
  pluginConfig?: Record<string, unknown>;
  logger: { debug?: (m: string) => void; warn: (m: string) => void; error: (m: string) => void; info?: (m: string) => void };
  registerTool: (tool: any, opts?: any) => void;
  on: (hook: string, handler: (...args: any[]) => any, opts?: { priority?: number }) => void;
  resolvePath: (input: string) => string;
}

const agentGluePlugin = {
  id: 'openclaw-agentglue',
  name: 'AgentGlue',
  version: '0.3.0',
  description: 'Cross-process dedup cache for multi-agent tool calls via SQLite sidecar',

  register(api: PluginApi) {
    const cfg: SidecarConfig = { ...DEFAULT_CONFIG, ...(api.pluginConfig || {}) as Partial<SidecarConfig> };
    const client = new SidecarClient(cfg.host, cfg.port);
    const log = (m: string) => (api.logger.info || api.logger.debug || console.log)(`[AgentGlue] ${m}`);
    const pluginDir = api.resolvePath('.');

    let manager: SidecarManager | null = null;

    // -- Lifecycle hooks --
    api.on('gateway_start', async () => {
      manager = new SidecarManager(cfg, pluginDir, client, log);
      if (cfg.autoStart) {
        try { await manager.start(); }
        catch (e: any) { api.logger.error(`Sidecar start failed: ${e.message}`); }
      }
    });

    api.on('gateway_stop', async () => {
      await manager?.stop();
    });

    // -- Auto-cache hook: capture ALL tool results --
    api.on('after_tool_call', async (event: any, ctx: any) => {
      if (event.error) return;
      if (!event.result) return;
      if (SKIP_TOOLS.has(event.toolName)) return;

      try {
        const params = event.params || {};
        const result = typeof event.result === 'string' ? event.result : JSON.stringify(event.result);
        await client.cacheStore(event.toolName, params, result, cfg.cacheTTL, ctx?.agentId);
      } catch {
        // Silently ignore cache store failures — never block the agent
      }
    });

    // -- Proxy tools that check cache first --
    const makeCachedTool = (toolName: string, description: string, paramsDef: any) => ({
      name: `agentglue_cached_${toolName}`,
      description: `[AgentGlue] ${description} — checks cross-agent cache first, falls back to sidecar execution.`,
      parameters: paramsDef,
      execute: async (_id: string, params: Record<string, unknown>) => {
        try {
          const cached = await client.cacheCheck(toolName === 'read' ? 'deduped_read_file' : toolName === 'search' ? 'deduped_search' : 'deduped_list_files', params);
          if (cached.hit) return `[cache hit, age=${cached.age_s}s]\n${cached.result}`;
        } catch { /* fall through */ }
        try {
          const sidecarTool = toolName === 'read' ? 'deduped_read_file' : toolName === 'search' ? 'deduped_search' : 'deduped_list_files';
          return await client.call(sidecarTool, params);
        } catch (e: any) {
          return `AgentGlue error: ${e.message}`;
        }
      },
    });

    api.registerTool(makeCachedTool('read', 'Read a file with cross-agent dedup cache', {
      type: 'object',
      properties: {
        file_path: { type: 'string', description: 'Absolute path to the file' },
        offset: { type: 'integer', description: 'Start line (1-indexed)', default: 1 },
        limit: { type: 'integer', description: 'Max lines to read', default: 200 },
      },
      required: ['file_path'],
    }));

    api.registerTool(makeCachedTool('search', 'Search a repo with cross-agent dedup cache', {
      type: 'object',
      properties: {
        repo_path: { type: 'string', description: 'Absolute path to the repository' },
        pattern: { type: 'string', description: 'Search pattern (regex)' },
        file_pattern: { type: 'string', description: 'File glob', default: '*' },
        max_results: { type: 'integer', description: 'Max results', default: 50 },
      },
      required: ['repo_path', 'pattern'],
    }));

    api.registerTool(makeCachedTool('list', 'List directory with cross-agent dedup cache', {
      type: 'object',
      properties: {
        dir_path: { type: 'string', description: 'Absolute path to directory' },
        recursive: { type: 'boolean', description: 'Recursive listing', default: false },
        include_hidden: { type: 'boolean', description: 'Include hidden files', default: false },
      },
      required: ['dir_path'],
    }));

    // -- Metrics tool --
    api.registerTool({
      name: 'agentglue_metrics',
      description: 'Get AgentGlue cache metrics (hit rate, dedup rate, cache size)',
      parameters: { type: 'object', properties: {} },
      execute: async () => {
        try {
          const stats = await client.cacheStats();
          return JSON.stringify(stats, null, 2);
        } catch (e: any) {
          return `AgentGlue metrics error: ${e.message}`;
        }
      },
    });

    // -- Health tool --
    api.registerTool({
      name: 'agentglue_health',
      description: 'Check AgentGlue sidecar health status',
      parameters: { type: 'object', properties: {} },
      execute: async () => {
        const ok = await client.healthCheck();
        return JSON.stringify({ healthy: ok, backend: 'sqlite', host: cfg.host, port: cfg.port, dbPath: cfg.dbPath || '~/.openclaw/cache/agentglue.db' });
      },
    });

    log('Plugin registered (v0.3.0, SQLite backend)');
  },
};

// Export both formats for compatibility
export default agentGluePlugin;
export { AgentGluePlugin, agentGluePlugin };
