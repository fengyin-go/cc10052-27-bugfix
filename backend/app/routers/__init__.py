"""业务路由汇总：领域专用路由先注册，通用资源路由兜底动态模块路径。"""
from __future__ import annotations

from app.routers import domain, resource

# domain 含 /api/segment_slice/... 等固定路径，必须在 resource 的
# /api/{module} 动态路由之前注册，否则固定路径会被通用列表接口抢先匹配。
ROUTERS = [domain, resource]
