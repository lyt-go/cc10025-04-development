"""业务路由汇总：费用交接专用路由在前，通用资源路由兜底。"""
from __future__ import annotations

from app.routers import cost_handoff, resource

# cost_handoff 含 /api/cost_handoff/bills 等固定子路径，
# 必须先于 resource 的 /api/{module} 动态路径注册。
ROUTERS = [cost_handoff, resource]
