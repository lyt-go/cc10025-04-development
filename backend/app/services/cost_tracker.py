"""cost_tracker 领域服务：成本追踪管理，接收 asset_catalog/cold_storage/hot_prefetch/backfill_job 上游事件。

跨模块数据流：
    asset_catalog → cost_tracker: 资产发布创建成本种子
    cold_storage → cost_tracker: 冷存迁移更新存储成本
    hot_prefetch → cost_tracker: 预热回迁更新传输成本
    partition_plan → backfill_job → cost_tracker: 补数更新计算成本
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any

from app.services.base import BaseModuleService
from app.store import store

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "assets")


class CostTrackerService(BaseModuleService):
    MODULE = "cost_tracker"
    ENTITY = "成本记录"
    OPERATION_TERM = "计算"
    LIST_FIELDS = ["记录编号", "关联资产", "存储成本", "计算成本", "传输成本", "统计周期", "趋势方向", "异常标记"]
    REQUIRED_FIELDS = ["记录编号", "关联资产", "存储成本"]
    STATUS_ORDER = ["草稿", "已核算", "已确认", "已归档"]
    ACTION_RULES = {"提交核算": "已核算", "确认成本": "已确认", "标记归档": "已归档"}
    NEGATIVE_ACTIONS = []

    def __init__(self):
        super().__init__()
        os.makedirs(DATA_DIR, exist_ok=True)

    def calculate_cost(self, entry_id: int) -> tuple[dict[str, Any] | None, str]:
        """核算成本：汇总存储、计算、传输三项成本。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return None, f"{self.ENTITY} {entry_id} 不存在"

        asset_id = str(entry.get("关联资产", ""))

        # 从其他模块收集成本信息
        storage_cost = self._estimate_storage_cost(asset_id)
        compute_cost = self._estimate_compute_cost(asset_id)
        transfer_cost = self._estimate_transfer_cost(asset_id)

        entry["存储成本"] = f"{storage_cost:.2f}"
        entry["计算成本"] = f"{compute_cost:.2f}"
        entry["传输成本"] = f"{transfer_cost:.2f}"

        total = storage_cost + compute_cost + transfer_cost
        entry["趋势方向"] = "上升" if total > 100 else "持平"
        entry["异常标记"] = "异常" if total > 500 else "无"
        entry["status"] = "已核算"
        entry["pending"] = False

        # 写入成本报告
        self._write_cost_report(asset_id, entry)

        return entry, f"{self.ENTITY} {entry_id} 已核算，总成本 {total:.2f}"

    def get_cost_report(self, asset_id: str) -> dict[str, Any] | None:
        """读取某资产的成本报告。"""
        filename = f"cost_report_{asset_id}.json"
        path = os.path.join(DATA_DIR, filename)
        if not os.path.exists(path):
            return None
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    def get_bill_summary(self, asset_id: str | None = None) -> dict[str, Any]:
        """费用交接分录聚合的域账单汇总，供成本追踪核对各域承担的费用。"""
        from app.services.cost_handoff import CostHandoffService

        handoff = CostHandoffService()
        bills = handoff.domain_bills()
        if asset_id:
            bills = [
                {
                    **bill,
                    "来源分录": [
                        item for item in bill.get("来源分录", [])
                        if str(item.get("关联资产")) == asset_id
                    ],
                }
                for bill in bills
            ]
            bills = [bill for bill in bills if bill["来源分录"]]
        return {
            "asset_id": asset_id or "ALL",
            "bills": bills,
            "totals": handoff.bill_totals(),
        }

    def list_asset_fees(self, asset_id: str) -> list[dict[str, Any]]:
        """列出某资产的整笔费用记录及其交接分录来源。"""
        from app.services.cost_handoff import CostHandoffService

        handoff = CostHandoffService()
        fees = handoff.list_fees(asset_id)
        entries = {
            str(row.get("费用编号")): row
            for row in store.rows("cost_handoff_entry")
        }
        return [
            {**fee, "交接分录": entries.get(str(fee.get("费用编号")))}
            for fee in fees
        ]

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        if action == "提交核算":
            return self.calculate_cost(entry_id)
        return super().run_action(entry_id, action)

    def _estimate_storage_cost(self, asset_id: str) -> float:
        """从冷存储任务估算存储成本。"""
        cold_rows = store.rows("cold_storage")
        for row in cold_rows:
            if str(row.get("源资产")) == asset_id:
                vol = str(row.get("迁移量", "0"))
                try:
                    return float(vol.replace("GB", "")) * 0.02 if "GB" in vol else 10.0
                except ValueError:
                    pass
        return 5.0

    def _estimate_compute_cost(self, asset_id: str) -> float:
        """从补数任务估算计算成本。"""
        backfill_rows = store.rows("backfill_job")
        for row in backfill_rows:
            if str(row.get("目标资产")) == asset_id:
                vol = str(row.get("数据量", "0"))
                try:
                    return float(vol.replace("GB", "")) * 0.05 if "GB" in vol else 20.0
                except ValueError:
                    pass
        return 10.0

    def _estimate_transfer_cost(self, asset_id: str) -> float:
        """从预热回迁任务估算传输成本。"""
        hot_rows = store.rows("hot_prefetch")
        for row in hot_rows:
            loc = str(row.get("冷存位置", ""))
            if asset_id in loc:
                vol = str(row.get("回迁量", "0"))
                try:
                    return float(vol.replace("GB", "")) * 0.09 if "GB" in vol else 15.0
                except ValueError:
                    pass
        return 3.0

    def _write_cost_report(self, asset_id: str, entry: dict[str, Any]) -> None:
        filename = f"cost_report_{asset_id}.json"
        path = os.path.join(DATA_DIR, filename)
        record = {
            "asset_id": asset_id,
            "record_id": str(entry.get("记录编号", "")),
            "storage_cost": str(entry.get("存储成本", "")),
            "compute_cost": str(entry.get("计算成本", "")),
            "transfer_cost": str(entry.get("传输成本", "")),
            "period": str(entry.get("统计周期", "")),
            "trend": str(entry.get("趋势方向", "")),
            "anomaly": str(entry.get("异常标记", "")),
            "calculated_at": datetime.now(timezone.utc).isoformat(),
            # 费用交接分录聚合的域账单：交接双方可据此核对计入本域的费用及来源
            "domain_bills": self.get_bill_summary(asset_id),
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(record, f, ensure_ascii=False, indent=2, default=str)