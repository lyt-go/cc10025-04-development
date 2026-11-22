"""费用交接与资产归属专用路由。

通用资源路由 /api/{module} 已覆盖列表/登记/动作；本路由只补充
通用骨架表达不了的聚合与溯源接口，必须在 resource 之前注册，
避免 /api/cost_handoff/... 被动态路径抢先匹配。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.services import service_for
from app.services.asset_ownership import AssetOwnershipService
from app.services.cost_handoff import CostHandoffService

router = APIRouter(prefix="/api", tags=["费用交接"])


def require_handoff() -> CostHandoffService:
    svc = service_for("cost_handoff")
    if svc is None:
        raise HTTPException(status_code=404, detail="费用交接服务未启用")
    assert isinstance(svc, CostHandoffService)
    return svc


def require_ownership() -> AssetOwnershipService:
    svc = service_for("asset_ownership")
    if svc is None:
        raise HTTPException(status_code=404, detail="资产归属服务未启用")
    assert isinstance(svc, AssetOwnershipService)
    return svc


class OwnerChangePayload(BaseModel):
    asset_id: str
    new_domain: str
    new_owner: str | None = None
    handoff_at: str


class ClosePeriodPayload(BaseModel):
    domain: str
    period: str


@router.get("/cost_handoff/bills")
def get_bills(
    scope: str | None = Query(default=None, description="权限内域，逗号分隔；为空返回全量"),
) -> dict[str, Any]:
    """域账单：用费用交接分录聚合，scope 模拟交接双方各自的数据权限。"""
    svc = require_handoff()
    scope_domains = [item.strip() for item in scope.split(",") if item.strip()] if scope else None
    return {
        "bills": svc.domain_bills(scope_domains),
        "scope_domains": scope_domains or [],
        "totals": svc.bill_totals(),
    }


@router.get("/cost_handoff/totals")
def get_totals() -> dict[str, Any]:
    """域间总额核对：费用总额=分录总额，且每笔费用只归属一个域。"""
    return require_handoff().bill_totals()


@router.get("/cost_handoff/fees/{fee_ref}/trace")
def trace_fee(fee_ref: str) -> dict[str, Any]:
    """费用溯源：分录回查到交接记录与补数计量记录。"""
    result = require_handoff().trace_fee(fee_ref)
    if result.get("entry") is None:
        raise HTTPException(status_code=404, detail=f"费用 {fee_ref} 尚无交接分录")
    return result


@router.post("/cost_handoff/settle")
def run_full_settlement() -> dict[str, Any]:
    """重新执行全量结算（幂等，已结账周期保持原依据）。"""
    return {"ok": True, "summary": require_handoff().full_settlement()}


@router.post("/cost_handoff/close-period")
def close_period(payload: ClosePeriodPayload) -> dict[str, Any]:
    """域周期结账：固化账单快照。"""
    return {"ok": True, "snapshot": require_handoff().close_period(payload.domain, payload.period)}


@router.get("/asset_ownership/timeline")
def get_timeline(
    asset_id: str | None = Query(default=None),
) -> dict[str, Any]:
    """资产归属时间线：带时间的归属片段，支持按资产过滤。"""
    return {"items": require_ownership().list_timeline(asset_id)}


@router.get("/asset_ownership/owner-at")
def get_owner_at(
    asset_id: str = Query(...),
    moment: str = Query(...),
) -> dict[str, Any]:
    """时间点回查资产归属域。"""
    svc = require_ownership()
    owner = svc.owner_at(asset_id, moment)
    if owner is None:
        raise HTTPException(status_code=404, detail=f"{moment} 无 {asset_id} 的归属记录")
    return owner


@router.post("/asset_ownership/change-owner")
def change_owner(payload: OwnerChangePayload) -> dict[str, Any]:
    """更改当前负责人：登记归属交接、生效并触发未结账费用重算。"""
    svc = require_ownership()
    entry, message = svc.change_owner(
        payload.asset_id,
        payload.new_domain,
        payload.new_owner or payload.new_domain,
        payload.handoff_at,
    )
    if entry is None:
        raise HTTPException(status_code=400, detail=message)
    handoff = require_handoff()
    return {
        "ok": True,
        "message": message,
        "entry": entry,
        "totals": handoff.bill_totals(),
        "bills": handoff.domain_bills(),
    }


@router.post("/asset_ownership/{entry_id}/apply")
def apply_handoff(entry_id: int) -> dict[str, Any]:
    """交接记录确认生效（动作接口的定制别名，返回重算后的账单）。"""
    svc = require_ownership()
    entry, message = svc.apply_handoff(entry_id)
    if entry is None:
        raise HTTPException(status_code=400, detail=message)
    handoff = require_handoff()
    return {"ok": True, "message": message, "entry": entry,
            "totals": handoff.bill_totals()}
