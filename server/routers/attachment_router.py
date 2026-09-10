"""附件、提及和运行兼容接口路由。"""

# 保留旧模块作为兼容层，新的注册入口使用业务含义明确的名称。
from server.routers.p1_router import p1 as attachments

__all__ = ["attachments"]
