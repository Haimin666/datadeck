"""TTL 缓存（阶段三 3.4）：元数据 + 高频答案缓存。

- MetaCache: OMD/表结构查询缓存（默认 10 分钟）；schema 结构稳定，可省重复网络往返。
- AnswerCache: 高频问题答案缓存（默认 30 分钟）；仅缓存"同一问题的确定性回答"。
- 进程内 LRU + TTL，零依赖；生产多实例可替换为 Redis（接口不变）。
"""

from __future__ import annotations

import hashlib
import threading
import time
from collections import OrderedDict
from typing import Any


class TTLCache:
    def __init__(self, max_size: int = 512, ttl_seconds: float = 600):
        self.max_size = max_size
        self.ttl = ttl_seconds
        self._store: OrderedDict[str, tuple[float, Any]] = OrderedDict()
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0

    def get(self, key: str) -> Any | None:
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                self.misses += 1
                return None
            ts, val = entry
            if time.monotonic() - ts > self.ttl:
                del self._store[key]
                self.misses += 1
                return None
            self._store.move_to_end(key)
            self.hits += 1
            return val

    def put(self, key: str, value: Any) -> None:
        with self._lock:
            self._store[key] = (time.monotonic(), value)
            self._store.move_to_end(key)
            while len(self._store) > self.max_size:
                self._store.popitem(last=False)

    def stats(self) -> dict:
        with self._lock:
            return {"size": len(self._store), "hits": self.hits, "misses": self.misses}

    def clear(self) -> None:
        with self._lock:
            self._store.clear()
            self.hits = self.misses = 0


# 元数据缓存（表结构/库表清单），10 分钟
meta_cache = TTLCache(max_size=256, ttl_seconds=600)

# 答案缓存（question → 回答文本），30 分钟
answer_cache = TTLCache(max_size=256, ttl_seconds=1800)


def _cache_key(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:32]


def cached_meta(key_parts: tuple, fn, *args, **kwargs):
    """同步元数据查询缓存包装。"""
    key = _cache_key(*map(str, key_parts))
    hit = meta_cache.get(key)
    if hit is not None:
        return {**hit, "_cached": True}
    result = fn(*args, **kwargs)
    if isinstance(result, dict) and result.get("ok"):
        meta_cache.put(key, result)
    return result


async def cached_meta_async(key_parts: tuple, coro_factory):
    """异步元数据查询缓存包装。"""
    key = _cache_key(*map(str, key_parts))
    hit = meta_cache.get(key)
    if hit is not None:
        return {**hit, "_cached": True}
    result = await coro_factory()
    if isinstance(result, dict) and result.get("ok"):
        meta_cache.put(key, result)
    return result


def answer_key(thread_uid: str, question: str) -> str:
    """答案缓存键：用户 + 归一化问题（去空白/大小写）。"""
    q_norm = " ".join(question.split()).lower()
    return _cache_key(thread_uid, q_norm)


def stats() -> dict:
    return {"meta": meta_cache.stats(), "answer": answer_cache.stats()}
