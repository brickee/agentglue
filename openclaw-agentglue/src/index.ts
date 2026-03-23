/**
 * OpenClaw AgentGlue Plugin v0.4.0
 *
 * Cross-process dedup cache for multi-agent tool calls.
 *
 * Architecture:
 *  - Exposes agentglue_cached_* proxy tools that check cross-agent cache first
 *  - Cache check → sidecar execution → Node.js fallback (graceful degradation)
 *  - after_tool_call hook auto-caches ALL read-only tool results under built-in names
 *  - Proxy tools read from the same cache keys as after_tool_call writes
 *  - Single sidecar process shared by all agents/sub-agents
 */

import * as http from 'http';
import * as fs from 'fs';
import { spawn, ChildProcess, execSync } from 'child_process';
import * as path from 'path';

// Cache key mapping: proxy tool suffix → original built-in tool name
// after_tool_call stores results under the built-in name (e.g. "read"),
// so proxy tools must check under the same key.
const BUILTIN_TOOL: Record<string, string> = {
  read: 'read',
  search: 'grep',
  list: 'glob',
  exec: 'bash',
  web_fetch: 'web_fetch',
  web_search: 'web_search',
};

// Sidecar tool name for each proxy tool
const SIDECAR_TOOL: Record<string, string> = {
  read: 'deduped_read_file',
  search: 'deduped_search',
  list: 'deduped_list_files',
  exec: 'deduped_exec',
  web_fetch: 'deduped_web_fetch',
  web_search: 'deduped_web_search',
};

// Tools that should never be cached (side-effectful or handled by us)
const SKIP_TOOLS = new Set([
  'edit', 'write', 'bash', 'exec', 'send_message', 'deliver',
  'send_telegram', 'send_whatsapp', 'send_discord',
  'notebook_edit', 'task_create', 'task_update',
  'agentglue_metrics', 'agentglue_health',
  // Our proxy tools — caching is handled inside their execute(),
  // so after_tool_call must not double-cache them.
  'agentglue_cached_read', 'agentglue_cached_search', 'agentglue_cached_list',
  'agentglue_cached_exec', 'agentglue_cached_web_fetch', 'agentglue_cached_web_search',
]);

// Write operations that ALWAYS invalidate cached reads.
// bash/exec are NOT here — they are checked by content (see _isMutatingBash).
const WRITE_TOOLS = new Set(['edit', 'write', 'notebook_edit']);

// The cached tool names that should be invalidated on writes
const READ_TOOL_NAMES = ['read', 'grep', 'glob'];

// Detect whether a bash/exec command is mutating (side-effectful).
// Read-only commands (git log, ls, cat, etc.) should NOT invalidate cache.
const READ_ONLY_COMMANDS = new Set([
  'git', 'ls', 'cat', 'head', 'tail', 'wc', 'file', 'stat',
  'which', 'env', 'echo', 'date', 'uname', 'whoami', 'pwd',
  'find', 'tree', 'du', 'df', 'rg', 'grep', 'awk', 'sed',
  'sort', 'uniq', 'diff', 'comm', 'cut', 'tr', 'column',
  'python3', 'python', 'node',  // may be read-only scripts, but be conservative
]);
function _isMutatingBash(params: Record<string, unknown>): boolean {
  const cmd = String(params.command || '').trim();
  if (!cmd) return false;
  // Check for obvious write patterns
  if (/[>]{1,2}/.test(cmd)) return true;  // redirections
  if (/\brm\b|\bmv\b|\bcp\b|\bmkdir\b|\brmdir\b|\btouch\b|\bchmod\b|\bchown\b|\bsudo\b|\bkill\b|\bdd\b/.test(cmd)) return true;
  // Check first command in pipeline
  const segments = cmd.split('|').map((s: string) => s.trim());
  const firstCmd = (segments[0] || '').split(/\s+/)[0].replace(/^.*\//, '');
  // If the leading command is known read-only, don't invalidate
  if (READ_ONLY_COMMANDS.has(firstCmd)) return false;
  // Unknown command — conservatively treat as mutating
  return true;
}

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

  async cacheInvalidate(toolNames: string[]): Promise<{ invalidated: number }> {
    return this.post('/cache/invalidate', { tool_names: toolNames });
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
  version: '0.4.0',
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

    // -- Auto-cache hook: capture ALL tool results + invalidate on writes --
    api.on('after_tool_call', async (event: any, ctx: any) => {
      if (event.error) return;

      // Write operations → invalidate all cached reads
      if (WRITE_TOOLS.has(event.toolName)) {
        try { await client.cacheInvalidate(READ_TOOL_NAMES); } catch {}
        return;
      }
      // bash/exec: only invalidate if the command looks mutating
      if ((event.toolName === 'bash' || event.toolName === 'exec') && _isMutatingBash(event.params || {})) {
        try { await client.cacheInvalidate(READ_TOOL_NAMES); } catch {}
        return;
      }

      if (!event.result) return;
      if (SKIP_TOOLS.has(event.toolName)) return;

      try {
        // Normalize params before storing so cache keys match proxy tool lookups
        const params = { ...(event.params || {}) };
        if (event.toolName === 'read' && params.path && !params.file_path) {
          params.file_path = params.path;
          delete params.path;
        }
        if ((event.toolName === 'grep' || event.toolName === 'search') && params.file_pattern && !params.glob) {
          params.glob = params.file_pattern;
          delete params.file_pattern;
        }
        const result = typeof event.result === 'string' ? event.result : JSON.stringify(event.result);
        await client.cacheStore(event.toolName, params, result, cfg.cacheTTL, ctx?.agentId);
      } catch {
        // Silently ignore cache store failures — never block the agent
      }
    });

    // -- Node.js fallbacks (used when sidecar is down) --
    // These must support the SAME parameters as the built-in tools so that
    // global deny of read/grep/glob does not lose any functionality.
    const nodeFallback: Record<string, (params: Record<string, unknown>) => string> = {
      read: (params) => {
        const filePath = String(params.file_path || params.path || '');
        const offset = Number(params.offset || 1);
        const limit = Number(params.limit || 200);
        const content = fs.readFileSync(filePath, 'utf-8');
        const lines = content.split('\n');
        const start = Math.max(0, offset - 1);
        return lines.slice(start, start + limit).map((l, i) => `${String(start + i + 1).padStart(6)}\t${l}`).join('\n');
      },
      search: (params) => {
        const pattern = String(params.pattern || '');
        const filePath = String(params.path || params.repo_path || '.');
        const args: string[] = ['--no-heading', '--line-number'];
        // Glob filter (accept both file_pattern and glob)
        const globFilter = params.glob || params.file_pattern;
        if (globFilter) args.push('--glob', String(globFilter));
        // File type filter
        if (params.type) args.push('--type', String(params.type));
        // Case insensitive
        if (params['-i'] || params.case_insensitive) args.push('-i');
        // Multiline
        if (params.multiline) args.push('-U', '--multiline-dotall');
        // Context lines
        if (params['-C'] || params.context) args.push('-C', String(Number(params['-C'] || params.context)));
        else {
          if (params['-A']) args.push('-A', String(Number(params['-A'])));
          if (params['-B']) args.push('-B', String(Number(params['-B'])));
        }
        // Output mode
        const mode = String(params.output_mode || 'content');
        if (mode === 'files_with_matches') args.push('-l');
        else if (mode === 'count') args.push('-c');
        // Max results / head limit
        const maxResults = params.max_results || params.head_limit;
        if (maxResults) args.push('--max-count', String(Number(maxResults)));
        args.push('--', pattern, filePath);
        try {
          return execSync('rg ' + args.map(a => JSON.stringify(a)).join(' '), { encoding: 'utf-8', timeout: 30000 });
        } catch (e: any) {
          return e.stdout || '(no matches)';
        }
      },
      list: (params) => {
        const dirPath = String(params.path || params.dir_path || '.');
        const globPattern = String(params.pattern || '*');
        try {
          const cmd = `find ${JSON.stringify(dirPath)} -name ${JSON.stringify(globPattern)} -maxdepth 10 2>/dev/null | head -500`;
          return execSync(cmd, { encoding: 'utf-8', timeout: 15000 }).trim() || '(no matches)';
        } catch (e: any) {
          return e.stdout || '(no matches)';
        }
      },
      exec: (params) => {
        const command = String(params.command || '');
        if (!command) return 'Error: No command provided';
        // Validate against whitelist (mirrors sidecar logic)
        const EXEC_WHITELIST = new Set([
          'git', 'ls', 'cat', 'head', 'tail', 'wc', 'file', 'stat',
          'which', 'env', 'echo', 'date', 'uname', 'whoami', 'pwd',
          'find', 'tree', 'du', 'df',
        ]);
        const GIT_SAFE = new Set([
          'log', 'status', 'diff', 'blame', 'show', 'branch', 'tag',
          'remote', 'rev-parse', 'describe', 'shortlog', 'ls-files',
          'ls-tree', 'config', 'reflog',
        ]);
        const DANGEROUS = /[>]{1,2}|\brm\b|\bmv\b|\bcp\b|\bsudo\b|\bchmod\b|\bchown\b|\bkill\b|\bdd\b|\bmkdir\b|\brmdir\b|\btouch\b|\btruncate\b/i;
        if (DANGEROUS.test(command)) return 'Error: Command contains disallowed pattern';
        const segments = command.split('|').map((s: string) => s.trim());
        for (const seg of segments) {
          if (!seg) continue;
          const parts = seg.split(/\s+/);
          const base = parts[0].replace(/^.*\//, '');
          if (!EXEC_WHITELIST.has(base)) return `Error: Command '${base}' is not in the read-only whitelist`;
          if (base === 'git' && parts.length > 1 && !parts[1].startsWith('-') && !GIT_SAFE.has(parts[1]))
            return `Error: Git sub-command '${parts[1]}' is not allowed`;
        }
        try {
          const timeout = Math.min(Number(params.timeout || 30) * 1000, 60000);
          return execSync(command, { encoding: 'utf-8', timeout }).trim() || '(empty output)';
        } catch (e: any) {
          return e.stdout || `Error: ${e.message}`;
        }
      },
      web_fetch: (params) => {
        // Node.js fallback using curl (synchronous)
        const url = String(params.url || '');
        if (!url) return 'Error: No URL provided';
        const args = ['-sS', '-L', '--max-time', '30', '-D', '-'];
        const headers = params.headers || {};
        if (typeof headers === 'object' && headers !== null) {
          for (const [k, v] of Object.entries(headers)) {
            args.push('-H', `${k}: ${v}`);
          }
        }
        args.push(url);
        try {
          const result = execSync('curl ' + args.map(a => JSON.stringify(a)).join(' '), { encoding: 'utf-8', timeout: 35000, maxBuffer: 10 * 1024 * 1024 });
          return result.slice(0, 100000) || '(empty response)';
        } catch (e: any) {
          return e.stdout || `Error fetching URL: ${e.message}`;
        }
      },
      web_search: (_params) => {
        return 'Web search not available in fallback mode. Route through the built-in web_search tool; results will be cached automatically by the after_tool_call hook.';
      },
    };

    // -- Proxy tools: agentglue_cached_* check cross-agent cache first --
    // Cache keys use the ORIGINAL built-in tool name (e.g. "read") so that
    // results cached by after_tool_call (which stores under event.toolName)
    // are found by these proxy tools.
    // TTL overrides per proxy tool (defaults to cfg.cacheTTL)
    const PROXY_TTL: Record<string, number> = {
      web_fetch: 120,
      web_search: 300,
    };

    const makeProxyTool = (proxyName: string, description: string, paramsDef: any) => ({
      name: `agentglue_cached_${proxyName}`,
      description: `[AgentGlue] ${description} — checks cross-agent cache first.`,
      parameters: paramsDef,
      execute: async (_id: string, params: Record<string, unknown>) => {
        const cacheKey = BUILTIN_TOOL[proxyName];  // e.g. "read" — same as after_tool_call stores
        const sidecarTool = SIDECAR_TOOL[proxyName];
        // Normalize parameter aliases so cache keys are consistent
        if (proxyName === 'read' && params.path && !params.file_path) {
          params = { ...params, file_path: params.path };
          delete (params as any).path;
        }
        if (proxyName === 'search' && params.file_pattern && !params.glob) {
          params = { ...params, glob: params.file_pattern };
        }
        // Serialize headers object to JSON string for sidecar compatibility
        if (proxyName === 'web_fetch' && params.headers && typeof params.headers === 'object') {
          params = { ...params, headers: JSON.stringify(params.headers) };
        }
        // 1. Check cross-agent cache (keyed by built-in tool name)
        try {
          const cached = await client.cacheCheck(cacheKey, params);
          if (cached.hit) return `[cache hit, age=${cached.age_s}s]\n${cached.result}`;
        } catch { /* fall through */ }
        // 2. Try sidecar execution
        const ttl = PROXY_TTL[proxyName] || cfg.cacheTTL;
        try {
          const result = await client.call(sidecarTool, params);
          // Store under built-in tool name for cross-agent benefit
          client.cacheStore(cacheKey, params, result, ttl).catch(() => {});
          return result;
        } catch { /* fall through */ }
        // 3. Node.js fallback (graceful degradation when sidecar is down)
        try {
          const result = nodeFallback[proxyName](params);
          client.cacheStore(cacheKey, params, result, ttl).catch(() => {});
          return result;
        } catch (e: any) {
          return `AgentGlue fallback error (${proxyName}): ${e.message}`;
        }
      },
    });

    api.registerTool(makeProxyTool('read', 'Read a file (cached across agents). Drop-in replacement for read.', {
      type: 'object',
      properties: {
        file_path: { type: 'string', description: 'Absolute path to the file' },
        path: { type: 'string', description: 'Alias for file_path' },
        offset: { type: 'integer', description: 'Start line (1-indexed)', default: 1 },
        limit: { type: 'integer', description: 'Max lines to read', default: 200 },
        pages: { type: 'string', description: 'Page range for PDF files (e.g. "1-5")' },
      },
      required: ['file_path'],
    }));

    api.registerTool(makeProxyTool('search', 'Search files by pattern (cached across agents). Drop-in replacement for grep.', {
      type: 'object',
      properties: {
        pattern: { type: 'string', description: 'Regular expression pattern to search for' },
        path: { type: 'string', description: 'File or directory to search in' },
        glob: { type: 'string', description: 'Glob pattern to filter files (e.g. "*.js")' },
        file_pattern: { type: 'string', description: 'Alias for glob' },
        type: { type: 'string', description: 'File type filter (js, py, rust, go, etc.)' },
        output_mode: { type: 'string', enum: ['content', 'files_with_matches', 'count'], description: 'Output mode', default: 'content' },
        '-i': { type: 'boolean', description: 'Case insensitive search' },
        '-A': { type: 'integer', description: 'Lines to show after each match' },
        '-B': { type: 'integer', description: 'Lines to show before each match' },
        '-C': { type: 'integer', description: 'Context lines before and after each match' },
        context: { type: 'integer', description: 'Alias for -C' },
        multiline: { type: 'boolean', description: 'Enable multiline matching' },
        '-n': { type: 'boolean', description: 'Show line numbers', default: true },
        head_limit: { type: 'integer', description: 'Limit output to first N entries' },
        max_results: { type: 'integer', description: 'Alias for head_limit' },
      },
      required: ['pattern'],
    }));

    api.registerTool(makeProxyTool('list', 'List files by glob pattern (cached across agents). Drop-in replacement for glob.', {
      type: 'object',
      properties: {
        pattern: { type: 'string', description: 'Glob pattern to match (e.g. "**/*.ts")', default: '*' },
        path: { type: 'string', description: 'Directory to search in' },
        dir_path: { type: 'string', description: 'Alias for path' },
        recursive: { type: 'boolean', description: 'Recursive listing', default: false },
        include_hidden: { type: 'boolean', description: 'Include hidden files', default: false },
      },
    }));

    // -- Exec proxy tool (custom TTL logic, not makeProxyTool) --
    const EXEC_TTL = (cmd: string): number => {
      const c = cmd.trim().toLowerCase();
      if (c.startsWith('git status')) return 10;
      if (c.startsWith('git diff')) return 15;
      if (c.startsWith('git log')) return 60;
      if (c.startsWith('git')) return 30;
      if (/^(ls|find|tree)\b/.test(c)) return 15;
      if (/^(date|env)\b/.test(c)) return 5;
      return 30;
    };

    api.registerTool({
      name: 'agentglue_cached_exec',
      description: '[AgentGlue] Execute a read-only command (cached across agents). Only allows safe, read-only commands from a whitelist (git log/status/diff/blame/show, ls, cat, head, tail, wc, file, stat, which, env, echo, date, uname, whoami, pwd, find, tree, du, df). Rejects rm, mv, cp, redirections, and other side-effectful operations.',
      parameters: {
        type: 'object',
        properties: {
          command: { type: 'string', description: 'The shell command to execute (must be read-only)' },
          timeout: { type: 'integer', description: 'Timeout in seconds (max 60)', default: 30 },
        },
        required: ['command'],
      },
      execute: async (_id: string, params: Record<string, unknown>) => {
        const command = String(params.command || '');
        const ttl = EXEC_TTL(command);
        const cacheKey = BUILTIN_TOOL['exec'];  // 'bash'
        // 1. Check cross-agent cache
        try {
          const cached = await client.cacheCheck(cacheKey, params);
          if (cached.hit) return `[cache hit, age=${cached.age_s}s]\n${cached.result}`;
        } catch { /* fall through */ }
        // 2. Try sidecar
        try {
          const result = await client.call(SIDECAR_TOOL['exec'], params);
          if (!result.startsWith('Error:')) {
            client.cacheStore(cacheKey, params, result, ttl).catch(() => {});
          }
          return result;
        } catch { /* fall through */ }
        // 3. Node.js fallback
        try {
          const result = nodeFallback['exec'](params);
          if (!result.startsWith('Error:')) {
            client.cacheStore(cacheKey, params, result, ttl).catch(() => {});
          }
          return result;
        } catch (e: any) {
          return `AgentGlue fallback error (exec): ${e.message}`;
        }
      },
    });

    // -- Web fetch proxy tool --
    api.registerTool(makeProxyTool('web_fetch', 'Fetch a URL (cached across agents). Drop-in replacement for web_fetch.', {
      type: 'object',
      properties: {
        url: { type: 'string', description: 'URL to fetch' },
        headers: { type: 'object', description: 'Optional HTTP headers as key-value pairs', additionalProperties: { type: 'string' } },
      },
      required: ['url'],
    }));

    // -- Web search proxy tool --
    api.registerTool(makeProxyTool('web_search', 'Search the web (cached across agents). Drop-in replacement for web_search.', {
      type: 'object',
      properties: {
        query: { type: 'string', description: 'Search query' },
        max_results: { type: 'integer', description: 'Maximum number of results', default: 5 },
      },
      required: ['query'],
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

    log('Plugin registered (v0.5.0, proxy tools + SQLite backend)');
  },
};

// Export both formats for compatibility
export default agentGluePlugin;
export { AgentGluePlugin, agentGluePlugin };
