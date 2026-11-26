"""分组分配器领域服务：管理实验用户的稳定分组、重新分配、剔除与批次核对。

数据链路位置：分组供给层，消费实验设计层下发的分组策略，为样本量核对与
曝光日志提供用户分组的权威数据，保证同一用户分组稳定。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.domain import (
    EXCLUDED_STATUS,
    bump_revision,
    current_revision,
    is_supported_slice,
    row_dimension_value,
    supported_dimensions,
)
from app.services.base import BaseModuleService
from app.services.segment_slice import SegmentSliceService
from app.store import store


class VariantAssignService(BaseModuleService):
    MODULE = "variant_assign"
    ENTITY = "分组结果"
    OPERATION_TERM = "校验"
    LIST_FIELDS = ["分配批次", "用户标识", "实验编码", "分组名称", "终端", "分配时间"]
    REQUIRED_FIELDS = ["分配批次", "用户标识", "实验编码"]
    STATUS_ORDER = ["已分配", "重新分配", "已剔除"]
    ACTION_RULES = {"重新分配": "重新分配", "剔除用户": "已剔除", "维度更正": "重新分配"}
    NEGATIVE_ACTIONS = []

    # ---- 业务扩展：用户分组与批次核对 ----

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return None, f"{self.ENTITY} {entry_id} 不存在或已归档"
        if action == "维度更正":
            # 该动作的目标维度/取值由专用接口携带，通用动作入口不直接受理
            return None, "维度更正请通过切片分析器的维度更正接口提交维度与取值"
        return super().run_action(entry_id, action)

    def correct_dimension(
        self, entry_id: int, dimension: str, new_value: str,
    ) -> dict[str, Any]:
        """成员维度更正：改维度取值、推进实验数据版次并冻结旧版次切片。

        典型场景：mobile 成员被更正到 desktop。更正后该实验旧版次的切片、
        凭证与集合全部冻结留痕，报告生成必须等待新版次重新计算确认。
        """
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return {"ok": False, "message": "分组记录不存在"}
        if entry.get("status") == EXCLUDED_STATUS:
            return {"ok": False, "message": "已剔除样本不能做维度更正，请先恢复分组"}
        if dimension not in supported_dimensions() or not is_supported_slice(dimension, new_value):
            return {
                "ok": False,
                "message": f"维度/取值非法：{dimension}={new_value}",
            }
        old_value = row_dimension_value(entry, dimension)
        if old_value == new_value:
            return {"ok": False, "message": f"维度 {dimension} 取值已是 {new_value}，无需更正"}
        exp_code = str(entry.get("实验编码", ""))
        entry[dimension] = new_value
        entry["status"] = "重新分配"
        entry["pending"] = True
        entry["分配时间"] = datetime.now(timezone.utc).isoformat()
        history = str(entry.get("变更历史", "") or "")
        entry["变更历史"] = (
            f"{history};{dimension}:{old_value}->{new_value}" if history
            else f"{dimension}:{old_value}->{new_value}"
        )
        new_revision = bump_revision(exp_code)
        freeze = SegmentSliceService().mark_stale_for_revision(exp_code, new_revision)
        return {
            "ok": True,
            "message": (
                f"用户 {entry.get('用户标识')} 的 {dimension} 已由 {old_value} 更正为 {new_value}，"
                f"实验数据版次推进到 {new_revision}；{freeze.get('message', '')}"
            ),
            "数据版次": new_revision,
        }

    def reassign_user(self, entry_id: int, new_group: str) -> dict[str, Any]:
        """重新分配用户分组并在原批次记录变更历史。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None or new_group not in ("对照组", "实验组"):
            return {"ok": False, "message": "记录不存在或目标分组非法"}
        old_group = entry.get("分组名称", "")
        entry["分组名称"] = new_group
        entry["status"] = "重新分配"
        entry["pending"] = True
        entry["分配时间"] = datetime.now(timezone.utc).isoformat()
        entry["变更历史"] = f"{old_group}->{new_group}"
        return {"ok": True, "message": f"用户 {entry.get('用户标识')} 由 {old_group} 重分配到 {new_group}"}

    def exclude_user(self, entry_id: int, reason: str) -> dict[str, Any]:
        """剔除用户并同步样本量核对批次更新实际比例。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return {"ok": False, "message": "记录不存在"}
        if not reason.strip():
            return {"ok": False, "message": "剔除必须填写原因"}
        entry["status"] = "已剔除"
        entry["pending"] = False
        entry["abnormal"] = True
        # 剔除会改变切片分母，同样推进数据版次并冻结旧切片
        exp_code = str(entry.get("实验编码", ""))
        new_revision = bump_revision(exp_code)
        SegmentSliceService().mark_stale_for_revision(exp_code, new_revision)
        ratio_rows = store.rows("sample_ratio")
        matched = [r for r in ratio_rows if r.get("实验编码") == exp_code and r.get("status") == "待核对"]
        for row in matched:
            row["偏移量"] = str(float(row.get("偏移量", "0") or 0) + 0.01)
            row["pending"] = True
        return {"ok": True, "message": f"用户已剔除（{reason}），数据版次推进到 {new_revision}，联动更新 {len(matched)} 条核对批次"}

    def batch_assign(self, exp_code: str, user_ids: list[str], strategy: str, terminal: str = "") -> dict[str, Any]:
        """按策略批量分配用户到对照组/实验组，可携带终端维度取值。"""
        if not user_ids or not exp_code:
            return {"ok": False, "message": "缺少实验编码或用户列表"}
        rows = store.rows(self.MODULE)
        batch_id = f"VA-{exp_code}-{datetime.now(timezone.utc).timestamp():.0f}"
        base_ratio = 0.5
        for idx, uid in enumerate(user_ids):
            group = "实验组" if (idx / max(len(user_ids), 1)) < base_ratio else "对照组"
            if strategy == "轮转分组":
                group = "实验组" if idx % 2 == 0 else "对照组"
            new_row = {
                "id": max((int(r.get("id", 0)) for r in rows), default=0) + 1,
                "分配批次": batch_id,
                "用户标识": uid,
                "实验编码": exp_code,
                "分组名称": group,
                "分配时间": datetime.now(timezone.utc).isoformat(),
                "status": "已分配",
                "pending": True,
                "abnormal": False,
            }
            if terminal:
                new_row["终端"] = terminal
            rows.append(new_row)
        return {"ok": True, "message": f"批次 {batch_id} 分配完成 {len(user_ids)} 人", "batch_id": batch_id}

    def list_experiment_members(self, exp_code: str) -> list[dict[str, Any]]:
        """列出实验全部分组成员（含已剔除），供切片页回查样本明细。"""
        return [
            {
                "id": row.get("id"),
                "用户标识": row.get("用户标识", ""),
                "分组名称": row.get("分组名称", ""),
                "终端": row_dimension_value(row, "终端"),
                "分配批次": row.get("分配批次", ""),
                "分配时间": row.get("分配时间", ""),
                "status": row.get("status", ""),
                "变更历史": row.get("变更历史", ""),
            }
            for row in store.rows(self.MODULE)
            if str(row.get("实验编码", "")) == str(exp_code)
        ]

    def current_revision(self, exp_code: str) -> dict[str, Any]:
        """读取实验当前数据版次，供切片页在计算前后核对。"""
        return {"ok": True, "实验编码": exp_code, "数据版次": current_revision(exp_code)}

    def produce_to_sample_ratio(self, exp_code: str) -> dict[str, Any]:
        """汇总实验实际分组比例并写入样本量核对批次。"""
        rows = [r for r in store.rows(self.MODULE) if r.get("实验编码") == exp_code and r.get("status") != "已剔除"]
        if not rows:
            return {"ok": False, "message": f"实验 {exp_code} 无有效分组数据"}
        exp_rows = sum(1 for r in rows if r.get("分组名称") == "实验组")
        actual_ratio = round(exp_rows / len(rows), 4)
        ratio_rows = store.rows("sample_ratio")
        ratio_rows.append({
            "id": max((int(r.get("id", 0)) for r in ratio_rows), default=0) + 1,
            "核对批次": f"SR-{exp_code}-{datetime.now(timezone.utc).timestamp():.0f}",
            "实验编码": exp_code,
            "计划比例": "0.5000",
            "实际比例": str(actual_ratio),
            "偏移量": str(round(abs(actual_ratio - 0.5), 4)),
            "status": "待核对",
            "pending": True,
            "abnormal": False,
        })
        return {"ok": True, "message": f"已写入核对批次，实际实验组比例 {actual_ratio}"}

    def consume_from_exp_design(self, exp_code: str) -> dict[str, Any]:
        """读取实验设计层方案，返回分组策略与流量比例。"""
        exp = next(
            (r for r in store.rows("exp_design") if r.get("实验编码") == exp_code),
            None,
        )
        if exp is None:
            return {"ok": False, "message": f"实验方案 {exp_code} 不存在"}
        return {
            "ok": True,
            "实验编码": exp_code,
            "分组策略": exp.get("分组策略", ""),
            "流量比例": exp.get("流量比例", ""),
            "状态": exp.get("status", ""),
        }

    def find_user_group(self, exp_code: str, user_id: str) -> dict[str, Any]:
        """定位用户在指定实验的当前分组，供曝光日志打标使用。"""
        matched = [
            r for r in store.rows(self.MODULE)
            if r.get("实验编码") == exp_code
            and r.get("用户标识") == user_id
            and r.get("status") != "已剔除"
        ]
        if not matched:
            return {"ok": False, "message": f"用户 {user_id} 在实验 {exp_code} 无分组"}
        latest = matched[-1]
        return {"ok": True, "分组名称": latest.get("分组名称", ""), "分配批次": latest.get("分配批次", "")}

    def batch_stats(self, exp_code: str) -> dict[str, Any]:
        """统计实验分组分布，输出给报告生成器引用。"""
        rows = [r for r in store.rows(self.MODULE) if r.get("实验编码") == exp_code]
        return {
            "实验编码": exp_code,
            "总分配数": len(rows),
            "已分配": sum(1 for r in rows if r.get("status") == "已分配"),
            "重新分配": sum(1 for r in rows if r.get("status") == "重新分配"),
            "已剔除": sum(1 for r in rows if r.get("status") == "已剔除"),
        }
