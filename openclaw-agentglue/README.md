# OpenClaw AgentGlue Plugin

> OpenClaw plugin for cross-process, cross-agent deduplicated caching via a lightweight Python sidecar backed by SQLite.

[![Version](https://img.shields.io/badge/version-0.3.0-blue.svg)](https://www.npmjs.com/package/openclaw-agentglue)

## What changed in v0.3

v0.3 turns the plugin into a self-contained npm package:
- Bundles the AgentGlue Python library inside the package
- Uses a SQLite-backed sidecar for cross-process cache sharing
- Auto-caches tool results after read-only calls
- Exposes cache-aware OpenClaw tools for repo exploration and file reads

No separate parent-project checkout is required at runtime.

## Features

- **SQLite-backed cross-agent cache** - cache survives across processes and agent sessions
- **Auto-managed sidecar** - starts automatically, includes health checks and restart handling
- **Exact-match dedup** - identical tool calls collapse to a shared cached result
- **Cache-aware repo tools** - read/search/list helpers for code exploration
- **Metrics + health endpoints** - inspect cache behavior and runtime status
- **Self-contained package** - bundled Python library, no extra AgentGlue install needed

## Install

Preferred:

```bash
npm install -g openclaw-agentglue
# or
openclaw plugins install openclaw-agentglue
```

For local development:

```bash
cd openclaw-agentglue
npm install
npm run build
npm run verify
```

## Requirements

- Node.js >= 18
- Python 3.10+
- OpenClaw with plugin support

## OpenClaw configuration

Add this to your OpenClaw config:

```json
{
  "plugins": {
    "openclaw-agentglue": {
      "host": "127.0.0.1",
      "port": 8765,
      "autoStart": true,
      "maxRestarts": 3,
      "restartDelayMs": 2000,
      "healthCheckIntervalMs": 30000,
      "cacheTTL": 300,
      "dbPath": ""
    }
  }
}
```

### Config options

| Option | Type | Default | Description |
|---|---|---:|---|
| `host` | string | `127.0.0.1` | Sidecar host to bind/connect to |
| `port` | integer | `8765` | Sidecar port |
| `autoStart` | boolean | `true` | Start sidecar automatically on gateway startup |
| `maxRestarts` | integer | `3` | Max automatic restart attempts |
| `restartDelayMs` | integer | `2000` | Delay between restart attempts |
| `healthCheckIntervalMs` | integer | `30000` | Sidecar health probe interval |
| `cacheTTL` | number | `300` | TTL in seconds for auto-cached tool results |
| `dbPath` | string | `""` | Optional SQLite DB path; empty uses `~/.openclaw/cache/agentglue.db` |

## Exposed OpenClaw tools

These are the tools OpenClaw users/agents actually call:

### `agentglue_cached_read`
Read a file with cross-agent cache lookup first.

```json
{
  "file_path": "/abs/path/to/file.py",
  "offset": 1,
  "limit": 200
}
```

### `agentglue_cached_search`
Search a repository with cache lookup first.

```json
{
  "repo_path": "/abs/path/to/repo",
  "pattern": "def.*train",
  "file_pattern": "*.py",
  "max_results": 50
}
```

### `agentglue_cached_list`
List files in a directory with cache lookup first.

```json
{
  "dir_path": "/abs/path/to/dir",
  "recursive": true,
  "include_hidden": false
}
```

### `agentglue_metrics`
Return cache and middleware metrics.

```json
{}
```

### `agentglue_health`
Return sidecar health and runtime config summary.

```json
{}
```

## Internal sidecar tools

The Python sidecar also defines internal tools (`deduped_read_file`, `deduped_search`, `deduped_list_files`) which back the public `agentglue_cached_*` tools. In normal OpenClaw usage, call the `agentglue_cached_*` names.

## Architecture

```text
OpenClaw gateway
  └─ AgentGlue plugin (TypeScript)
      ├─ after_tool_call hook stores cached results
      ├─ registers agentglue_cached_* tools
      └─ manages Python sidecar lifecycle
             └─ SQLite-backed AgentGlue runtime
```

## Verify before release

```bash
npm run build
npm run verify
npm pack --dry-run
```

## Troubleshooting

### Sidecar does not start
```bash
python3 --version
python3 sidecar/server.py --host 127.0.0.1 --port 8765
```

### Port conflict
```bash
lsof -i :8765
```

### Clean rebuild
```bash
rm -rf dist node_modules python
npm install
npm run build
npm run verify
```

## License

MIT
