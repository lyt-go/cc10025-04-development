"""asset_ownership 领域服务：资产归属交接记录与归属时间线。

跨模块数据流：
    asset_catalog → asset_ownership: 登记资产的初始归属
    asset_ownership → cost_handoff: 交接生效后触发未结账费用重新归属
    cost_handoff → asset_ownership: 按交接点时间回查资产归属域

归属时间线（asset_ownership_timeline）按资产保存带生效时间的归属片段，
任意时刻的资产归属域都可以通过时间点回查得到，作为费用归属分录的依据。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from app.services.base import BaseModuleService
from app.store import store


def parse_dt(value: str | datetime | None) -> datetime | None:
    """宽松解析 ISO 日期/时间字符串，日期按当天 00:00:00 处理。"""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    text = str(value).strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        try:
            return datetime.strptime(text[:10], "%Y-%m-%d")
        except ValueError:
            return None


def period_of(value: str | datetime | None) -> str:
    """从时间值提取统计周期 YYYY-MM。"""
    moment = parse_dt(value)
    if moment is None:
        return ""
    return moment.strftime("%Y-%m")


class AssetOwnershipService(BaseModuleService):
    """资产归属交接：交接记录 + 归属时间线，回答“某个时点资产归哪个域”。"""

    MODULE = "asset_ownership"
    ENTITY = "归属交接"
    OPERATION_TERM = "交接"
    LIST_FIELDS = [
        "交接编号", "关联资产", "交出域", "接收域", "交接点时间",
        "原负责人", "新负责人", "交接状态",
    ]
    REQUIRED_FIELDS = ["交接编号", "关联资产", "交出域", "接收域", "交接点时间"]
    STATUS_ORDER = ["待生效", "已生效", "已撤回"]
    ACTION_RULES = {"确认生效": "已生效", "撤回交接": "已撤回"}
    NEGATIVE_ACTIONS = []

    # 归属时间线表名（不在 18 个业务模块清单内，仅由本服务维护）
    TIMELINE_MODULE = "asset_ownership_timeline"

    def list_timeline(self, asset_id: str | None = None) -> list[dict[str, Any]]:
        """查看归属时间线，可按资产过滤。"""
        rows = store.rows(self.TIMELINE_MODULE)
        if asset_id:
            rows = [row for row in rows if str(row.get("关联资产")) == asset_id]
        return sorted(rows, key=lambda row: str(row.get("生效时间")))

    def owner_at(self, asset_id: str, moment: str | datetime) -> dict[str, Any] | None:
        """时间点回查：返回 moment 时刻该资产的归属时间线条目。

        取生效时间不晚于 moment 的最后一条；没有则返回 None。
        """
        target = parse_dt(moment)
        if target is None:
            return None
        candidates = [
            row for row in self.list_timeline(asset_id)
            if (parse_dt(row.get("生效时间")) or datetime.min) <= target
        ]
        return candidates[-1] if candidates else None

    def current_owner(self, asset_id: str) -> dict[str, Any] | None:
        """当前归属：取时间线上最后一条。"""
        rows = self.list_timeline(asset_id)
        return rows[-1] if rows else None

    def domains_for_asset(self, asset_id: str) -> list[str]:
        """资产在时间线上出现过的全部归属域（按首次出现顺序）。"""
        seen: list[str] = []
        for row in self.list_timeline(asset_id):
            domain = str(row.get("归属域", ""))
            if domain and domain not in seen:
                seen.append(domain)
        return seen

    def apply_handoff(self, entry_id: int) -> tuple[dict[str, Any] | None, str]:
        """交接生效：写时间线、更新资产当前归属、触发未结账费用重算。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return None, f"{self.ENTITY} {entry_id} 不存在"
        if entry.get("status") == "已生效":
            return entry, f"{self.ENTITY} {entry_id} 已生效，无需重复确认"
        if entry.get("status") == "已撤回":
            return None, f"{self.ENTITY} {entry_id} 已撤回，不能再生效"

        asset_id = str(entry.get("关联资产", ""))
        handoff_at = str(entry.get("交接点时间", ""))
        new_domain = str(entry.get("接收域", ""))
        new_owner = str(entry.get("新负责人", "")) or new_domain

        entry["status"] = "已生效"
        entry["pending"] = False
        entry["交接状态"] = "已生效"

        # 1. 写入带时间的归属时间线（费用归属回查的依据）
        timeline_rows = store.rows(self.TIMELINE_MODULE)
        timeline_rows.append({
            "id": max((int(row.get("id", 0)) for row in timeline_rows), default=0) + 1,
            "关联资产": asset_id,
            "归属域": new_domain,
            "负责人": new_owner,
            "生效时间": handoff_at,
            "来源交接编号": str(entry.get("交接编号", "")),
            "时间线条目状态": "生效",
        })

        # 2. 同步资产编目中的当前归属域与负责人
        for asset in store.rows("asset_catalog"):
            if str(asset.get("资产编号")) == asset_id:
                asset["所属域"] = new_domain
                asset["负责人"] = new_owner
                asset["最近更新"] = handoff_at
                break

        # 3. 触发成本侧对该资产未结账费用重新归属（已结账周期保持原依据）
        from app.services.cost_handoff import CostHandoffService
        svc = CostHandoffService()
        summary = svc.resettle_asset(asset_id, reason_handoff_id=entry_id)
        entry["重算结果"] = summary
        return entry, (
            f"{self.ENTITY} {entry_id} 已生效，资产 {asset_id} 自 {handoff_at} 起归属 {new_domain}；"
            f"费用交接分录重算 {summary.get('settled', 0)} 笔，其中已结账周期冻结 {summary.get('frozen', 0)} 笔"
        )

    def change_owner(self, asset_id: str, new_domain: str, new_owner: str,
                     handoff_at: str) -> tuple[dict[str, Any] | None, str]:
        """登记一笔归属交接并立即生效，供“更改当前负责人”场景一步完成。"""
        if not asset_id or not new_domain:
            return None, "资产编号与接收域不能为空"
        if parse_dt(handoff_at) is None:
            return None, f"交接点时间格式无法识别：{handoff_at}"

        current = self.current_owner(asset_id)
        old_domain = str(current.get("归属域", "")) if current else ""
        old_owner = str(current.get("负责人", "")) if current else ""
        if old_domain == new_domain:
            return None, f"资产 {asset_id} 当前已归属 {new_domain}，无需交接"

        rows = store.rows(self.MODULE)
        entry = {
            "id": max((int(row.get("id", 0)) for row in rows), default=0) + 1,
            "交接编号": f"HAND-{max((int(row.get('id', 0)) for row in rows), default=0) + 1:04d}",
            "关联资产": asset_id,
            "交出域": old_domain,
            "接收域": new_domain,
            "交接点时间": handoff_at,
            "原负责人": old_owner,
            "新负责人": new_owner or new_domain,
            "交接状态": "待生效",
            "status": "待生效",
            "pending": True,
            "abnormal": False,
        }
        rows.append(entry)
        applied, message = self.apply_handoff(entry["id"])
        return applied, message

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        if action == "确认生效":
            return self.apply_handoff(entry_id)
        if action == "撤回交接":
            entry = store.find(self.MODULE, entry_id)
            if entry is None:
                return None, f"{self.ENTITY} {entry_id} 不存在"
            if entry.get("status") == "已生效":
                return None, f"{self.ENTITY} {entry_id} 已生效并写入归属时间线，不能撤回"
        return super().run_action(entry_id, action)
