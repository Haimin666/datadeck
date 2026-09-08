"""datadeck 日志工具：收敛自 Yuxi 的 utils.logger（此处用标准 logging 简化）。

生产宿主可替换为 loguru/统一日志，但本库不依赖具体实现。
"""

from __future__ import annotations

import logging

_handler = logging.StreamHandler()
_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))

logger = logging.getLogger("datadeck")
logger.setLevel(logging.INFO)
if not logger.handlers:
    logger.addHandler(_handler)
    logger.propagate = False