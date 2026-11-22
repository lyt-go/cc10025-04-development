"""跨模块服务容器：提供统一的交叉索引和上游消费入口。"""
from __future__ import annotations

from typing import TYPE_CHECKING

from app.services.asset_catalog import AssetCatalogService
from app.services.tier_policy import TierPolicyService
from app.services.lineage_graph import LineageGraphService
from app.services.retention_rule import RetentionRuleService
from app.services.compliance_check import ComplianceCheckService
from app.services.cold_storage import ColdStorageService
from app.services.hot_prefetch import HotPrefetchService
from app.services.version_snapshot import VersionSnapshotService
from app.services.label_tag import LabelTagService
from app.services.audit_log import AuditLogService
from app.services.access_gate import AccessGateService
from app.services.cost_tracker import CostTrackerService
from app.services.schema_registry import SchemaRegistryService
from app.services.partition_plan import PartitionPlanService
from app.services.backfill_job import BackfillJobService
from app.services.quality_report import QualityReportService
from app.services.reclaim_job import ReclaimJobService
from app.services.change_notice import ChangeNoticeService
from app.services.asset_ownership import AssetOwnershipService
from app.services.cost_handoff import CostHandoffService

_SERVICE_CLASSES = [
    AssetCatalogService,
    TierPolicyService,
    LineageGraphService,
    RetentionRuleService,
    ComplianceCheckService,
    ColdStorageService,
    HotPrefetchService,
    VersionSnapshotService,
    LabelTagService,
    AuditLogService,
    AccessGateService,
    CostTrackerService,
    SchemaRegistryService,
    PartitionPlanService,
    BackfillJobService,
    QualityReportService,
    ReclaimJobService,
    ChangeNoticeService,
    # 资产归属交接先于费用交接分录实例化：费用归属回查依赖归属时间线
    AssetOwnershipService,
    CostHandoffService,
]

_INSTANCES: dict[str, object] = {}

for cls in _SERVICE_CLASSES:
    instance = cls()
    _INSTANCES[cls.MODULE] = instance

# 费用交接分录的 HTTP 模块路径是 cost_handoff，数据表为 cost_handoff_entry，
# 这里补一个路径别名，让资源路由按 cost_handoff 也能取到服务。
_INSTANCES["cost_handoff"] = _INSTANCES["cost_handoff_entry"]


def service_for(module: str) -> object | None:
    """按模块key返回领域服务实例。"""
    return _INSTANCES.get(module)


def all_services() -> list:
    return list(_INSTANCES.values())


# 服务容器就绪后做一次全量费用结算，生成费用交接分录；
# 已结账周期（种子中的 cost_closed_period）按原依据冻结归属。
_handoff = _INSTANCES.get("cost_handoff")
if isinstance(_handoff, CostHandoffService):
    _handoff.full_settlement()
    for _closed in list(_handoff.domain_bills()):
        if _closed.get("冻结分录数"):
            _handoff.close_period(str(_closed["归属域"]), str(_closed["统计周期"]))


__all__ = [
    "service_for",
    "all_services",
    "AssetCatalogService",
    "TierPolicyService",
    "LineageGraphService",
    "RetentionRuleService",
    "ComplianceCheckService",
    "ColdStorageService",
    "HotPrefetchService",
    "VersionSnapshotService",
    "LabelTagService",
    "AuditLogService",
    "AccessGateService",
    "CostTrackerService",
    "SchemaRegistryService",
    "PartitionPlanService",
    "BackfillJobService",
    "QualityReportService",
    "ReclaimJobService",
    "ChangeNoticeService",
    "AssetOwnershipService",
    "CostHandoffService",
]