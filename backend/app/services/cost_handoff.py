"""cost_handoff 领域服务：费用交接分录、域账单聚合与跨域费用归属。

业务规则：
    1. 成本追踪里每笔费用（cost_fee）只有整笔金额，可能横跨资产归属交接点。
    2. 已结账周期（cost_closed_period）继续按原依据复算，归属域冻结不变。
    3. 尚未结账的每笔费用只能计给一个域：按“完成/计量时间 → 发生时间”的顺序
       取归属时间点，回查 asset_ownership_timeline 得到当时具备资产归属的域。
    4. 每笔费用生成一条费用交接分录（cost_handoff_entry），分录上保留交接编号、
       计量记录编号等溯源字段，可回查到该次交接和补数的计量记录。
    5. 用分录聚合各域账单，交接双方在各自权限（scope_domains）内只能看到
       计入本域的费用及来源，且同一笔费用不会在两个域重复出现。

跨模块数据流：
    backfill_job → cost_metering: 补数发起/完成形成带时间的计量记录
    cost_metering → cost_fee: 计量结果生成整笔费用
    asset_ownership → cost_handoff: 交接点时间线决定费用归属域
    cost_handoff_entry → 域账单: 分录按域/周期聚合
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any

from app.services.asset_ownership import AssetOwnershipService, parse_dt, period_of
from app.services.base import BaseModuleService
from app.store import store

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "assets")

# 各表模块 key（在 seed 中预置，部分表不挂通用资源路由）
FEE_MODULE = "cost_fee"                 # 费用记录（整笔金额）
METERING_MODULE = "cost_metering"       # 带时间的计算/计量数据
ENTRY_MODULE = "cost_handoff_entry"     # 费用交接分录
CLOSED_MODULE = "cost_closed_period"    # 已结账周期
BILL_FILE_PREFIX = "domain_bill_"


class CostHandoffService(BaseModuleService):
    """费用交接：把整笔费用按资产归属时间线唯一地计给一个域。"""

    # MODULE 指向分录表；HTTP 路径 /api/cost_handoff 由专用路由与资源路由共同承载
    MODULE = ENTRY_MODULE
    ENTITY = "费用交接分录"
    OPERATION_TERM = "结算"
    LIST_FIELDS = [
        "分录编号", "关联资产", "费用编号", "归属域", "统计周期",
        "费用金额", "归属时间点", "归属依据",
    ]
    REQUIRED_FIELDS = ["分录编号", "关联资产", "费用编号", "归属域", "统计周期", "费用金额"]
    STATUS_ORDER = ["待结算", "已结算"]
    ACTION_RULES: dict[str, str] = {}
    NEGATIVE_ACTIONS = []

    def __init__(self) -> None:
        super().__init__()
        os.makedirs(DATA_DIR, exist_ok=True)
        self._ownership = AssetOwnershipService()

    # ------------------------------------------------------------------
    # 计量与费用查询
    # ------------------------------------------------------------------
    def list_metering(self, asset_id: str | None = None) -> list[dict[str, Any]]:
        rows = store.rows(METERING_MODULE)
        if asset_id:
            rows = [row for row in rows if str(row.get("关联资产")) == asset_id]
        return sorted(rows, key=lambda row: str(row.get("计量时间")))

    def list_fees(self, asset_id: str | None = None) -> list[dict[str, Any]]:
        rows = store.rows(FEE_MODULE)
        if asset_id:
            rows = [row for row in rows if str(row.get("关联资产")) == asset_id]
        return list(rows)

    def list_entries(self, *, filters=None, keyword=None, status=None,
                     page=1, size=20) -> tuple[list[dict[str, Any]], int]:
        # 分录的状态在「分录状态」字段上，通用骨架读 status，这里补齐兼容。
        for row in store.rows(ENTRY_MODULE):
            row.setdefault("status", row.get("分录状态", "已结算"))
        return super().list_entries(
            filters=filters, keyword=keyword, status=status, page=page, size=size)

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(ENTRY_MODULE, entry_id)

    # ------------------------------------------------------------------
    # 归属判定
    # ------------------------------------------------------------------
    def _closed_domains(self, period: str) -> set[str]:
        return {
            str(row.get("归属域")) for row in store.rows(CLOSED_MODULE)
            if str(row.get("统计周期")) == period and str(row.get("结账状态")) == "已结账"
        }

    def _attribution_moment(self, fee: dict[str, Any]) -> tuple[str, str]:
        """决定费用归属所用的时间点与来源。

        优先用完成时间/计量时间（补数在新域完成的场景），
        其次退回费用发生时间。
        """
        metering_id = str(fee.get("计量记录编号", ""))
        if metering_id:
            for meter in store.rows(METERING_MODULE):
                if str(meter.get("计量编号")) == metering_id:
                    if str(meter.get("完成时间", "")).strip():
                        return str(meter.get("完成时间")), "计量完成时间"
                    if str(meter.get("计量时间", "")).strip():
                        return str(meter.get("计量时间")), "计量记录时间"
        if str(fee.get("完成时间", "")).strip():
            return str(fee.get("完成时间")), "费用完成时间"
        return str(fee.get("发生时间", "")), "费用发生时间"

    def resolve_domain(self, asset_id: str, moment: str) -> tuple[str, dict[str, Any] | None]:
        """按时间点回查归属时间线，返回（归属域, 时间线条目）。"""
        owner = self._ownership.owner_at(asset_id, moment)
        if owner is None:
            return "", None
        return str(owner.get("归属域", "")), owner

    # ------------------------------------------------------------------
    # 结算：费用 → 交接分录
    # ------------------------------------------------------------------
    def settle_fee(self, fee_id: int, *, reason_handoff_id: int | None = None) -> dict[str, Any] | None:
        """结算单笔费用，生成或更新唯一一条费用交接分录。

        已结账周期按原依据复算（归属域冻结）；未结账费用按归属时间点
        唯一计给当时具备资产归属的域。
        """
        fee = store.find(FEE_MODULE, fee_id)
        if fee is None:
            return None

        asset_id = str(fee.get("关联资产", ""))
        period = str(fee.get("统计周期", "")) or period_of(fee.get("发生时间"))
        amount = float(fee.get("费用金额", 0) or 0)
        entries = store.rows(ENTRY_MODULE)
        existing = next(
            (row for row in entries if str(row.get("费用编号")) == str(fee.get("费用编号"))),
            None,
        )

        moment, moment_source = self._attribution_moment(fee)
        domain, owner_row = self.resolve_domain(asset_id, moment)
        metering_id = str(fee.get("计量记录编号", ""))
        handoff_ref = str(owner_row.get("来源交接编号", "")) if owner_row else ""

        closed_domains = self._closed_domains(period)
        frozen = False
        basis_note: str
        if existing is not None and str(existing.get("归属域")) in closed_domains:
            # 已结账周期：继续按原依据复算，归属域冻结
            frozen = True
            domain = str(existing.get("归属域"))
            basis_note = (
                f"周期 {period} 已结账，按原依据复算并冻结归属域 {domain}"
            )
            moment = str(existing.get("归属时间点")) or moment
            moment_source = str(existing.get("归属时间点来源", "")) or moment_source
            handoff_ref = str(existing.get("来源交接编号", "")) or handoff_ref
            metering_id = str(existing.get("计量记录编号", "")) or metering_id
        elif existing is None and domain in closed_domains:
            # 首次结算即落在已结账周期：按当前依据建账并立即冻结
            frozen = True
            basis_note = (
                f"周期 {period} 已结账，按 {moment_source} {moment} 的归属依据"
                f"建账并冻结归属域 {domain}"
            )
        else:
            if not domain:
                basis_note = f"归属时间点 {moment} 无可用资产归属记录，暂挂待结算"
            else:
                basis_note = (
                    f"按{moment_source} {moment} 回查资产归属时间线，"
                    f"资产 {asset_id} 归属 {domain}"
                )

        trace = {
            "费用编号": str(fee.get("费用编号", "")),
            "归属时间点": moment,
            "归属时间点来源": moment_source,
            "来源交接编号": handoff_ref,
            "计量记录编号": metering_id,
            "补数作业编号": str(fee.get("补数作业编号", "")),
            "触发交接id": reason_handoff_id,
        }

        if existing is None:
            entry = {
                "id": max((int(row.get("id", 0)) for row in entries), default=0) + 1,
                "分录编号": f"HENT-{max((int(row.get('id', 0)) for row in entries), default=0) + 1:04d}",
                "关联资产": asset_id,
                "费用编号": str(fee.get("费用编号", "")),
                "归属域": domain or "待确定",
                "统计周期": period,
                "费用金额": f"{amount:.2f}",
                "归属时间点": moment,
                "归属时间点来源": moment_source,
                "归属依据": basis_note,
                "来源交接编号": handoff_ref,
                "计量记录编号": metering_id,
                "补数作业编号": str(fee.get("补数作业编号", "")),
                "费用类型": str(fee.get("费用类型", "")),
                "分录状态": "已结算" if domain else "待结算",
                "冻结标记": frozen,
                "溯源": trace,
            }
            entries.append(entry)
        else:
            existing.update({
                "归属域": domain or str(existing.get("归属域", "待确定")),
                "统计周期": period,
                "费用金额": f"{amount:.2f}",
                "归属时间点": moment,
                "归属时间点来源": moment_source,
                "归属依据": basis_note,
                "来源交接编号": handoff_ref,
                "计量记录编号": metering_id,
                "补数作业编号": str(fee.get("补数作业编号", "")),
                "费用类型": str(fee.get("费用类型", "")),
                "分录状态": "已结算" if domain else "待结算",
                "冻结标记": frozen,
                "溯源": trace,
            })
            entry = existing
        entry["status"] = entry["分录状态"]
        return entry

    def full_settlement(self) -> dict[str, Any]:
        """对全部费用执行一次结算（幂等：同一费用只保留一条分录）。"""
        fees = store.rows(FEE_MODULE)
        settled = 0
        frozen = 0
        pending = 0
        for fee in fees:
            entry = self.settle_fee(int(fee.get("id", 0)))
            if entry is None:
                continue
            settled += 1
            if entry.get("冻结标记"):
                frozen += 1
            if entry.get("分录状态") == "待结算":
                pending += 1
        return {"settled": settled, "frozen": frozen, "pending": pending,
                "entries": len(store.rows(ENTRY_MODULE))}

    def resettle_asset(self, asset_id: str, *, reason_handoff_id: int | None = None) -> dict[str, Any]:
        """资产交接后重算该资产全部费用；已结账周期冻结，未结账重新归属。"""
        fee_ids = [
            int(fee.get("id", 0)) for fee in store.rows(FEE_MODULE)
            if str(fee.get("关联资产")) == asset_id
        ]
        settled = 0
        frozen = 0
        for fee_id in fee_ids:
            entry = self.settle_fee(fee_id, reason_handoff_id=reason_handoff_id)
            if entry is None:
                continue
            settled += 1
            if entry.get("冻结标记"):
                frozen += 1
        return {"asset": asset_id, "settled": settled, "frozen": frozen,
                "open_resettled": settled - frozen}

    # ------------------------------------------------------------------
    # 域账单聚合（带权限范围）
    # ------------------------------------------------------------------
    def domain_bills(self, scope_domains: list[str] | None = None) -> list[dict[str, Any]]:
        """用费用交接分录聚合各域账单。

        scope_domains 模拟交接双方各自的数据权限：为空表示全量；
        非空时只返回权限内域的账单，保证双方只能核对计入本域的费用。
        """
        scope = {d for d in (scope_domains or []) if d}
        grouped: dict[tuple[str, str], dict[str, Any]] = {}
        for entry in store.rows(ENTRY_MODULE):
            domain = str(entry.get("归属域", ""))
            if scope and domain not in scope:
                continue
            period = str(entry.get("统计周期", ""))
            key = (domain, period)
            bucket = grouped.setdefault(key, {
                "归属域": domain,
                "统计周期": period,
                "费用总额": 0.0,
                "分录数": 0,
                "冻结分录数": 0,
                "来源分录": [],
            })
            bucket["费用总额"] += float(entry.get("费用金额", 0) or 0)
            bucket["分录数"] += 1
            if entry.get("冻结标记"):
                bucket["冻结分录数"] += 1
            bucket["来源分录"].append({
                "分录编号": entry.get("分录编号"),
                "费用编号": entry.get("费用编号"),
                "关联资产": entry.get("关联资产"),
                "费用金额": entry.get("费用金额"),
                "费用类型": entry.get("费用类型"),
                "归属依据": entry.get("归属依据"),
                "来源交接编号": entry.get("来源交接编号"),
                "计量记录编号": entry.get("计量记录编号"),
                "冻结标记": entry.get("冻结标记", False),
            })
        bills = list(grouped.values())
        for bill in bills:
            bill["费用总额"] = round(bill["费用总额"], 2)
        bills.sort(key=lambda item: (item["归属域"], item["统计周期"]))
        return bills

    def bill_totals(self) -> dict[str, Any]:
        """域间总额核对：各域合计应等于全部费用金额，且每笔费用只出现一次。"""
        bills = self.domain_bills()
        domain_totals = {
            domain: round(sum(
                bill["费用总额"] for bill in bills if bill["归属域"] == domain
            ), 2)
            for domain in {bill["归属域"] for bill in bills}
        }
        fee_total = round(sum(
            float(fee.get("费用金额", 0) or 0) for fee in store.rows(FEE_MODULE)
        ), 2)
        entry_total = round(sum(
            float(entry.get("费用金额", 0) or 0) for entry in store.rows(ENTRY_MODULE)
        ), 2)
        fee_refs = [str(entry.get("费用编号")) for entry in store.rows(ENTRY_MODULE)]
        return {
            "domain_totals": domain_totals,
            "fee_total": fee_total,
            "entry_total": entry_total,
            "balanced": abs(fee_total - entry_total) < 0.005,
            "duplicate_entries": len(fee_refs) - len(set(fee_refs)),
        }

    # ------------------------------------------------------------------
    # 结账与溯源
    # ------------------------------------------------------------------
    def close_period(self, domain: str, period: str) -> dict[str, Any]:
        """结账：固化该域该周期的账单快照，后续复算只能沿用原依据。"""
        rows = store.rows(CLOSED_MODULE)
        record = next(
            (row for row in rows
             if str(row.get("归属域")) == domain and str(row.get("统计周期")) == period),
            None,
        )
        bills = [
            bill for bill in self.domain_bills()
            if bill["归属域"] == domain and bill["统计周期"] == period
        ]
        snapshot = {
            "domain": domain,
            "period": period,
            "closed_at": datetime.now(timezone.utc).isoformat(),
            "bills": bills,
            "total": round(sum(bill["费用总额"] for bill in bills), 2),
        }
        if record is None:
            record = {
                "id": max((int(row.get("id", 0)) for row in rows), default=0) + 1,
                "归属域": domain,
                "统计周期": period,
                "结账状态": "已结账",
                "结账时间": snapshot["closed_at"],
                "快照文件": f"{BILL_FILE_PREFIX}{domain}_{period}.json",
            }
            rows.append(record)
        else:
            record["结账状态"] = "已结账"
            record["结账时间"] = snapshot["closed_at"]
        self._write_bill_snapshot(snapshot)
        return snapshot

    def trace_fee(self, fee_ref: str) -> dict[str, Any]:
        """费用溯源：分录 → 交接记录 + 计量记录 + 费用本体，全部可回查。"""
        fee_ref = str(fee_ref)
        entry = next(
            (row for row in store.rows(ENTRY_MODULE)
             if str(row.get("费用编号")) == fee_ref),
            None,
        )
        fee = next(
            (row for row in store.rows(FEE_MODULE)
             if str(row.get("费用编号")) == fee_ref),
            None,
        )
        metering = None
        if fee and fee.get("计量记录编号"):
            metering = next(
                (row for row in store.rows(METERING_MODULE)
                 if str(row.get("计量编号")) == str(fee.get("计量记录编号"))),
                None,
            )
        handoff = None
        ref = entry.get("来源交接编号") if entry else None
        if ref:
            handoff = next(
                (row for row in store.rows(self._ownership.MODULE)
                 if str(row.get("交接编号")) == str(ref)),
                None,
            )
        return {"fee": fee, "entry": entry, "metering": metering, "handoff": handoff}

    def _write_bill_snapshot(self, snapshot: dict[str, Any]) -> str:
        filename = f"{BILL_FILE_PREFIX}{snapshot['domain']}_{snapshot['period']}.json"
        path = os.path.join(DATA_DIR, filename)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(snapshot, f, ensure_ascii=False, indent=2, default=str)
        return path
