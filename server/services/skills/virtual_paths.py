"""Skill 虚拟路径契约（自 Yuxi agents/backends/paths.py 抽取照搬）。

datadeck 无 Sandbox Backend；虚拟路径仅作为提示词中告知模型的 Skill 文件位置约定，
读取由 SkillsMiddleware 门控的 read_file 类工具按投影目录解析（与 Yuxi 语义一致）。
"""

from __future__ import annotations

from server.workspace.paths import VIRTUAL_PATH_PREFIX

VIRTUAL_SKILLS_PATH = "/home/gem/skills"
VIRTUAL_PERSONAL_SKILLS_PATH = f"{VIRTUAL_PATH_PREFIX.rstrip('/')}/agents/skills"

__all__ = [
    "VIRTUAL_PATH_PREFIX",
    "VIRTUAL_SKILLS_PATH",
    "VIRTUAL_PERSONAL_SKILLS_PATH",
]
