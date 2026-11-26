"""切片与报告的专用领域接口。

通用资源路由只承载列表/登记/动作，涉及维度选样、版次确认、凭证领用、
维度更正与下钻回查的业务动作在这里显式建模，路由层不做业务判断。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.services import service_for
from app.services.report_gen import ReportGenService
from app.services.segment_slice import SegmentSliceService
from app.services.variant_assign import VariantAssignService

router = APIRouter(prefix="/api", tags=["切片与报告领域"])


class SliceCreatePayload(BaseModel):
    切片批次: str
    实验编码: str
    切片维度: str
    切片值: str


class CorrectDimensionPayload(BaseModel):
    维度: str
    新取值: str


class RebindPayload(BaseModel):
    切片集合编码: str


def _slice_service() -> SegmentSliceService:
    service = service_for("segment_slice")
    assert isinstance(service, SegmentSliceService)
    return service


def _report_service() -> ReportGenService:
    service = service_for("report_gen")
    assert isinstance(service, ReportGenService)
    return service


def _assign_service() -> VariantAssignService:
    service = service_for("variant_assign")
    assert isinstance(service, VariantAssignService)
    return service


def _result(payload: dict[str, Any]) -> dict[str, Any]:
    """统一返回壳：业务字段保留在 data，避免与顶层 ok/message 重复嵌套。"""
    return {
        "ok": bool(payload.get("ok")),
        "message": payload.get("message", ""),
        "data": {key: value for key, value in payload.items() if key not in ("ok", "message")},
    }


# ---- 维度注册表与实验版次 ----

@router.get("/segment_slice/dimensions/catalog")
def dimension_catalog() -> dict[str, Any]:
    return _slice_service().dimension_catalog()


@router.get("/variant_assign/experiments/{exp_code}/revision")
def experiment_revision(exp_code: str) -> dict[str, Any]:
    return _result(_assign_service().current_revision(exp_code))


@router.get("/variant_assign/experiments/{exp_code}/members")
def experiment_members(exp_code: str) -> dict[str, Any]:
    return {"ok": True, "items": _assign_service().list_experiment_members(exp_code)}


@router.post("/variant_assign/{entry_id}/correct-dimension")
def correct_dimension(entry_id: int, payload: CorrectDimensionPayload) -> dict[str, Any]:
    return _result(
        _assign_service().correct_dimension(entry_id, payload.维度, payload.新取值)
    )


# ---- 切片计算、确认与集合 ----

@router.post("/segment_slice/slices")
def register_slice(payload: SliceCreatePayload) -> dict[str, Any]:
    entry, missing = _slice_service().create_entry(payload.model_dump())
    if missing:
        return {"ok": False, "message": "；".join(missing), "data": None}
    return {"ok": True, "message": "切片已登记，等待开始计算", "data": entry}


@router.post("/segment_slice/{entry_id}/run")
def run_slice(entry_id: int) -> dict[str, Any]:
    return _result(_slice_service().run_slice(entry_id))


@router.post("/segment_slice/{entry_id}/confirm")
def confirm_slice(entry_id: int) -> dict[str, Any]:
    return _result(_slice_service().confirm_version(entry_id))


@router.post("/segment_slice/experiments/{exp_code}/slice-sets")
def build_slice_set(exp_code: str) -> dict[str, Any]:
    return _result(_slice_service().build_slice_set(exp_code))


@router.get("/segment_slice/experiments/{exp_code}/slice-sets")
def list_slice_sets(exp_code: str) -> dict[str, Any]:
    return {"ok": True, "items": _slice_service().list_slice_sets(exp_code)}


@router.get("/segment_slice/experiments/{exp_code}/credentials")
def list_credentials(exp_code: str) -> dict[str, Any]:
    return {"ok": True, "items": _slice_service().list_credentials(exp_code)}


@router.get("/segment_slice/credentials/{credential_code}/samples")
def credential_samples(credential_code: str) -> dict[str, Any]:
    return _result(_slice_service().credential_samples(credential_code))


# ---- 报告领用、复算与下钻 ----

@router.post("/report_gen/experiments/{exp_code}/queue-drilldown")
def queue_drilldown_report(exp_code: str) -> dict[str, Any]:
    return _result(_slice_service().produce_to_report_gen(exp_code))


@router.post("/report_gen/{entry_id}/rebind-slice-set")
def rebind_slice_set(entry_id: int, payload: RebindPayload) -> dict[str, Any]:
    return _result(_report_service().rebind_slice_set(entry_id, payload.切片集合编码))


@router.post("/report_gen/{entry_id}/replay")
def replay_report(entry_id: int) -> dict[str, Any]:
    return _result(_report_service().replay_report(entry_id))


@router.get("/report_gen/{entry_id}/drilldown")
def report_drilldown(entry_id: int) -> dict[str, Any]:
    return _result(_report_service().drilldown_detail(entry_id))
