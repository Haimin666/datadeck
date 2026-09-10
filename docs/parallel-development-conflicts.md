# 并行开发协调记录

> Last updated: 2026-09-09 16:50 +08:00

## Ownership

- **DataDeck platform adapter**: Project / Workspace / Viewer / operation logs / route registration.
- **Other concurrent work**: Redis manager, queue, request persistence, model provider cache internals, Skills / MCP / task runtime.
- Redis and queue implementations are intentionally not modified by the platform adapter work.

## Resolved conflicts

- Viewer routes were independently created as both:
  - `server/routers/filesystem_router.py`
  - `server/routers/viewer_filesystem_router.py`
- Resolution: `server/routers/filesystem_router.py` is the canonical implementation.
  `server/routers/viewer_filesystem_router.py` now only re-exports it. This preserves both file
  paths during parallel development and avoids duplicate FastAPI route registration.

## Shared files needing manual review before commit

- `server/main.py`
- `server/models.py`
- `server/routers/__init__.py`
- `server/deps.py`
- `server/routers/chat_router.py`
- `pyproject.toml`

## Verification constraints

- Local PostgreSQL tests are blocked in this sandbox with `Operation not permitted`.
- Redis is available locally, but model cache internals are owned by concurrent work and were not changed.
- `python -m compileall -q server src tests` passed after the viewer route merge.
