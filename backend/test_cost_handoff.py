"""费用交接分录专项验收测试。

覆盖需求场景：
    1. 交接点前后各一笔 + 横跨交接点一笔，共三笔费用结账；
    2. 已结账周期按原依据复算（归属冻结），未结账每笔费用只计给一个域；
    3. 归属依据可回查到该次交接与补数的计量记录；
    4. 费用交接分录聚合各域账单，双方在各自权限内核对；
    5. 更改当前负责人后，域间总额与历史账单仍对得上。
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from app.services import service_for
from app.store import store

RED = "\033[91m"
GREEN = "\033[92m"
RESET = "\033[0m"

passed = 0
failed = 0


def check(desc, condition, detail=""):
    global passed, failed
    if condition:
        print(f"  {GREEN}PASS{RESET} {desc}")
        passed += 1
        return True
    print(f"  {RED}FAIL{RESET} {desc} {detail}")
    failed += 1
    return False


def entry_of(fee_ref: str) -> dict:
    return next(
        row for row in store.rows("cost_handoff_entry")
        if str(row.get("费用编号")) == fee_ref
    )


def test_seed_data_shape():
    print("\n=== 1. 场景数据：交接点前/后/横跨交接点三笔费用 ===")
    fees = store.rows("cost_fee")
    check("费用记录共 3 笔", len(fees) == 3, f"实际 {len(fees)} 笔")

    timeline = service_for("asset_ownership").list_timeline("ASSE-0004")
    check("资产归属时间线含 2 个带时间片段", len(timeline) == 2)
    check("交接点为 2026-10-15",
          any(str(row.get("来源交接编号")) == "HAND-0001" for row in timeline))

    metering = store.rows("cost_metering")
    cross = next(row for row in metering if str(row.get("计量编号")) == "METR-0003")
    check("横跨交接点的补数计量在旧域发起", cross["发起域"] == "华北数据域")
    check("横跨交接点的补数计量在新域完成", cross["完成域"] == "华东数据域")
    check("计量记录关联补数作业 BACK-0004", cross["补数作业编号"] == "BACK-0004")


def test_initial_settlement():
    print("\n=== 2. 首次结账：三笔费用的归属域 ===")
    svc = service_for("cost_handoff")
    summary = svc.full_settlement()
    check("全量结算覆盖 3 笔费用", summary["settled"] == 3, str(summary))

    e1 = entry_of("FEE-0001")
    e2 = entry_of("FEE-0002")
    e3 = entry_of("FEE-0003")
    # 交接点（10-15）之前一笔 → 旧域
    check("交接点前费用 FEE-0001 计入华北数据域", e1["归属域"] == "华北数据域",
          str(e1["归属域"]))
    # 交接点之后一笔 → 新域
    check("交接点后费用 FEE-0002 计入华东数据域", e2["归属域"] == "华东数据域",
          str(e2["归属域"]))
    # 横跨交接点的整笔费用 → 按完成时间唯一计给新域，不拆分、不双计
    check("横跨交接点费用 FEE-0003 整笔计入华东数据域", e3["归属域"] == "华东数据域",
          str(e3["归属域"]))
    check("横跨费用按计量完成时间定归属", e3["归属时间点来源"] == "计量完成时间")
    check("横跨费用归属时间点为补数完成时刻",
          e3["归属时间点"] == "2026-10-18T16:00:00")


def test_single_attribution_no_conflict():
    print("\n=== 3. 每笔费用只归属一个域，域间不产生冲突 ===")
    svc = service_for("cost_handoff")
    refs = [str(row.get("费用编号")) for row in store.rows("cost_handoff_entry")]
    check("分录与费用一一对应、无重复归属", len(refs) == len(set(refs)) == 3)

    totals = svc.bill_totals()
    check("费用总额 520.00", totals["fee_total"] == 520.0, str(totals["fee_total"]))
    check("分录总额与费用总额相等", totals["balanced"], str(totals))
    check("重复归属分录数为 0", totals["duplicate_entries"] == 0)
    check("华北域合计 100.00", totals["domain_totals"].get("华北数据域") == 100.0)
    check("华东域合计 420.00", totals["domain_totals"].get("华东数据域") == 420.0)


def test_closed_period_frozen():
    print("\n=== 4. 已结账周期按原依据复算并冻结 ===")
    svc = service_for("cost_handoff")
    e1_before = dict(entry_of("FEE-0001"))
    svc.full_settlement()  # 再次复算（幂等）
    e1_after = entry_of("FEE-0001")
    check("已结账费用带冻结标记", e1_after["冻结标记"] is True)
    check("复算后归属域保持华北数据域",
          e1_after["归属域"] == e1_before["归属域"] == "华北数据域")
    check("归属依据注明已结账冻结", "已结账" in str(e1_after["归属依据"]))

    snapshot_path = os.path.join(
        os.path.dirname(__file__), "app", "data", "assets",
        "domain_bill_华北数据域_2026-09.json",
    )
    check("历史账单快照文件已生成", os.path.exists(snapshot_path))

    # 即使人为再插入一个晚于费用时点的交接，已结账费用也不动
    own = service_for("asset_ownership")
    own.change_owner("ASSE-0004", "华南数据域", "华南域负责人", "2026-09-20T00:00:00")
    e1_frozen = entry_of("FEE-0001")
    check("插入新交接后已结账费用仍留华北域",
          e1_frozen["归属域"] == "华北数据域", str(e1_frozen["归属域"]))


def test_traceable_basis():
    print("\n=== 5. 归属依据可回查到交接记录与补数计量记录 ===")
    svc = service_for("cost_handoff")
    trace = svc.trace_fee("FEE-0003")
    check("溯源返回费用本体", trace["fee"] is not None)
    check("溯源返回交接分录", trace["entry"] is not None)
    check("溯源返回补数计量记录 METR-0003",
          trace["metering"] is not None
          and str(trace["metering"].get("计量编号")) == "METR-0003")
    check("溯源返回交接记录 HAND-0001",
          trace["handoff"] is not None
          and str(trace["handoff"].get("交接编号")) == "HAND-0001")
    check("分录上保留来源交接编号",
          str(trace["entry"].get("来源交接编号")) == "HAND-0001")
    check("分录上保留补数作业编号 BACK-0004",
          str(trace["entry"].get("补数作业编号")) == "BACK-0004")


def test_domain_bills_with_scope():
    print("\n=== 6. 分录聚合域账单，双方按各自权限核对 ===")
    svc = service_for("cost_handoff")
    north = svc.domain_bills(["华北数据域"])
    east = svc.domain_bills(["华东数据域"])
    check("华北视角只看到本域账单",
          len(north) == 1 and north[0]["归属域"] == "华北数据域")
    check("华东视角只看到本域账单",
          len(east) == 1 and east[0]["归属域"] == "华东数据域")
    check("华北账单含费用来源分录",
          north[0]["来源分录"][0]["费用编号"] == "FEE-0001")
    check("华东账单含两笔来源分录",
          {item["费用编号"] for item in east[0]["来源分录"]} == {"FEE-0002", "FEE-0003"})
    # 无权限的域看不到对方费用
    check("华北视角看不到华东费用",
          all(bill["归属域"] == "华北数据域" for bill in north))

    all_bills = svc.domain_bills()
    check("全量账单共 2 个域周期", len(all_bills) == 2)


def test_change_owner_reconciliation():
    print("\n=== 7. 更改当前负责人后域间总额与历史账单核对 ===")
    svc = service_for("cost_handoff")
    own = service_for("asset_ownership")

    before = svc.bill_totals()
    entry, message = own.change_owner(
        "ASSE-0004", "西南数据域", "西南域负责人", "2026-11-10T00:00:00")
    check("负责人更改交接登记并生效", entry is not None and entry["status"] == "已生效",
          message)

    asset = next(row for row in store.rows("asset_catalog")
                 if row["资产编号"] == "ASSE-0004")
    check("资产当前归属同步为西南数据域", asset["所属域"] == "西南数据域")
    check("资产当前负责人同步更新", asset["负责人"] == "西南域负责人")

    after = svc.bill_totals()
    check("更改负责人后总额仍然平衡", after["balanced"], str(after))
    check("更改前后费用总额一致", before["fee_total"] == after["fee_total"] == 520.0)
    check("更改后无重复归属", after["duplicate_entries"] == 0)

    # 历史账单（已结账的华北 2026-09）金额不变
    history = svc.domain_bills(["华北数据域"])
    check("华北历史账单仍为 100.00",
          len(history) == 1 and history[0]["费用总额"] == 100.0)

    # 时间点回查：历史时点归属由时间线决定，当前时点指向新域
    owner_1001 = own.owner_at("ASSE-0004", "2026-10-01T00:00:00")
    check("2026-10-01 时点可回查到明确归属", owner_1001 is not None)
    check("2026-10-01 时点归属来自测试4插入的华南交接",
          owner_1001["归属域"] == "华南数据域", str(owner_1001.get("归属域")))
    check("2026-10-20 时点仍归属华东",
          own.owner_at("ASSE-0004", "2026-10-20T00:00:00")["归属域"] == "华东数据域")
    check("2026-11-11 时点归属西南",
          own.owner_at("ASSE-0004", "2026-11-11T00:00:00")["归属域"] == "西南数据域")


def test_open_fee_follows_new_owner():
    print("\n=== 8. 补充验证：未结账费用在归属时点已换新域时跟随新域 ===")
    svc = service_for("cost_handoff")
    # 新增一笔 11 月发生的未结账费用，归属时点已在西南域期间
    fees = store.rows("cost_fee")
    fees.append({
        "id": 4,
        "费用编号": "FEE-0004",
        "关联资产": "ASSE-0004",
        "费用类型": "存储费",
        "费用金额": "80.00",
        "统计周期": "2026-11",
        "发生时间": "2026-11-12T08:00:00",
        "完成时间": "2026-11-12T08:30:00",
        "计量记录编号": "",
        "补数作业编号": "",
        "费用状态": "已入账",
    })
    entry = svc.settle_fee(4)
    check("新费用唯一计入西南数据域", entry["归属域"] == "西南数据域",
          str(entry["归属域"]))
    totals = svc.bill_totals()
    check("加入新费用后总额平衡为 600.00",
          totals["balanced"] and totals["fee_total"] == 600.0, str(totals))
    check("新费用不影响华北历史账单",
          totals["domain_totals"].get("华北数据域") == 100.0)


def test_cost_tracker_bill_summary():
    print("\n=== 9. 成本追踪侧可读取费用交接聚合账单 ===")
    tracker = service_for("cost_tracker")
    summary = tracker.get_bill_summary("ASSE-0004")
    check("成本追踪返回域账单列表", len(summary["bills"]) >= 2)
    check("汇总中含平衡核对结果", summary["totals"]["balanced"] is True)
    asset_fees = tracker.list_asset_fees("ASSE-0004")
    check("资产费用均挂接交接分录",
          all(fee.get("交接分录") is not None for fee in asset_fees))


if __name__ == "__main__":
    print("=" * 60)
    print("费用交接分录专项验收测试")
    print("=" * 60)

    test_seed_data_shape()
    test_initial_settlement()
    test_single_attribution_no_conflict()
    test_closed_period_frozen()
    test_traceable_basis()
    test_domain_bills_with_scope()
    test_change_owner_reconciliation()
    test_open_fee_follows_new_owner()
    test_cost_tracker_bill_summary()

    print(f"\n{'=' * 60}")
    print(f"结果: {GREEN}{passed} 通过{RESET} / {RED}{failed} 失败{RESET} / "
          f"{passed + failed} 总计")
    print("=" * 60)
    if failed > 0:
        sys.exit(1)
