from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from datadeck.agents.toolkits.service import get_tool_descriptors
from datadeck.agents.toolkits.packages import mcp_package_options
from datadeck.ports.tools import ToolDescriptor
from server.db import get_db
from server.deps import get_required_user
from server.models import User
from server.services.mcp.service import get_all_mcp_servers

tools = APIRouter(prefix="/system/tools", tags=["tools"])


@tools.get("")
async def list_tools(
    category: str = None,
    user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    """获取工具列表"""
    descriptors = get_tool_descriptors(category)
    if category in (None, "mcp"):
        descriptors.extend(
            ToolDescriptor(
                slug=item["slug"], name=item["name"], description=item["description"],
                kind="package", group="mcp", package_slug=item["package_slug"],
                source="postgres", category="mcp", version="1", configurable=True,
                visible=True, metadata=item.get("metadata", {}),
            )
            for item in mcp_package_options(
                [item for item in await get_all_mcp_servers(db) if bool(item.enabled)]
            )
        )
    items = [item.to_dict() for item in descriptors if item.visible]
    return {"items": items, "total": len(items)}
