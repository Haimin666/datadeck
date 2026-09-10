"""远程 Skill 安装（datadeck 版）。

Yuxi 的远程安装依赖沙箱 ProvisionerSandboxBackend（npx skills add + 目录拉回宿主）。
datadeck 不部署沙箱，远程安装入口禁用（返回友好错误）；本地 zip 上传安装不受影响。
后续若需要远程安装，可改用 httpx 直连 skills registry API 或宿主进程内 npx。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from datadeck import logger


class RemoteSkillInstallDisabled(RuntimeError):
    """datadeck 未部署沙箱，远程 Skill 安装不可用。"""


@dataclass(frozen=True)
class _PreparationResult:
    slug: str = ""
    success: bool = False
    error: str = ""
    source_dir: str = ""


@dataclass(frozen=True)
class RemoteSkillsBatchPreparation:
    draft_id: str = ""
    results: list[_PreparationResult] = field(default_factory=list)


async def list_remote_skills(source: str) -> list[dict[str, str]]:
    message = "远程 Skill 列表需要 Sandbox 运行时，datadeck 当前部署未启用；请使用本地 zip 上传安装"
    logger.warning(message)
    raise RemoteSkillInstallDisabled(message)


async def search_remote_skills(query: str) -> list[dict[str, str]]:
    message = "远程 Skill 搜索需要 Sandbox 运行时，datadeck 当前部署未启用；请使用本地 zip 上传安装"
    logger.warning(message)
    raise RemoteSkillInstallDisabled(message)


async def prepare_remote_skills_batch(
    source: str,
    skills: list[str],
    uid: str,
) -> RemoteSkillsBatchPreparation:
    message = "远程 Skill 安装需要 Sandbox 运行时，datadeck 当前部署未启用；请使用本地 zip 上传安装"
    logger.warning(message)
    # 逐项返回失败结果，让 draft 确认接口把原因透传给前端
    return RemoteSkillsBatchPreparation(
        draft_id="",
        results=[
            _PreparationResult(slug=str(name), success=False, error=message) for name in skills
        ],
    )
