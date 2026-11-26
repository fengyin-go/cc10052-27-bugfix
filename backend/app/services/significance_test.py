"""显著性检验领域服务：管理实验分组差异的P值、置信区间计算与检验批次流转。

数据链路位置：统计检验层，消费指标计算层产出的指标结果，对分组差异执行
显著性检验，并把显著结果推送给归因分析与切片分析继续下钻。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.services.base import BaseModuleService
from app.store import store


class SignificanceTestService(BaseModuleService):
    MODULE = "significance_test"
    ENTITY = "检验结果"
    OPERATION_TERM = "校验"
    LIST_FIELDS = ["检验批次", "实验编码", "指标编码", "P值", "置信区间"]
    REQUIRED_FIELDS = ["检验批次", "实验编码", "指标编码"]
    STATUS_ORDER = ["待检验", "已检验", "检验异常"]
    ACTION_RULES = {"执行检验": "已检验", "重试检验": "检验异常"}
    NEGATIVE_ACTIONS = []

    # ---- 业务扩展：检验执行与结果流转 ----

    def run_test(self, entry_id: int) -> dict[str, Any]:
        """执行显著性检验，P值低于阈值时登记归因分析批次。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None or entry.get("status") != "待检验":
            return {"ok": False, "message": "仅待检验批次可执行"}
        p_value = str(entry.get("P值", "1.0"))
        try:
            p_num = float(p_value)
        except ValueError:
            entry["status"] = "检验异常"
            entry["abnormal"] = True
            return {"ok": False, "message": "P值格式非法，批次转入检验异常"}
        entry["status"] = "已检验"
        entry["pending"] = False
        if p_num < 0.05:
            attr_rows = store.rows("attr_analysis")
            attr_rows.append({
                "id": max((int(r.get("id", 0)) for r in attr_rows), default=0) + 1,
                "归因批次": f"AT-{entry_id}-{datetime.now(timezone.utc).timestamp():.0f}",
                "实验编码": entry.get("实验编码", ""),
                "贡献因子": entry.get("指标编码", ""),
                "贡献权重": "0.62",
                "归因方法": "shapley",
                "status": "排队中",
                "pending": True,
                "abnormal": False,
            })
            return {"ok": True, "message": f"检验显著（P={p_num}），已登记归因批次"}
        return {"ok": True, "message": f"检验不显著（P={p_num}），无需归因"}

    def retry_abnormal(self, entry_id: int) -> dict[str, Any]:
        """重试检验异常批次，重新解析P值与置信区间。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None or entry.get("status") != "检验异常":
            return {"ok": False, "message": "仅检验异常批次可重试"}
        ci = str(entry.get("置信区间", ""))
        if "[" not in ci or "," not in ci:
            entry["置信区间"] = "[-0.05, 0.05]"
        entry["status"] = "待检验"
        entry["pending"] = True
        entry["abnormal"] = False
        return {"ok": True, "message": f"批次 {entry_id} 已重置为待检验"}

    def batch_run_pending(self) -> dict[str, Any]:
        """批量执行全部待检验批次并推送显著结果。"""
        pending_rows = [r for r in store.rows(self.MODULE) if r.get("status") == "待检验"]
        significant = 0
        for row in pending_rows:
            result = self.run_test(int(row.get("id", 0)))
            if result.get("ok") and "显著" in str(result.get("message", "")) and "不显著" not in str(result.get("message", "")):
                significant += 1
        return {"ok": True, "message": f"批量检验完成，显著批次 {significant}", "total": len(pending_rows)}

    def produce_to_segment_slice(self, exp_code: str) -> dict[str, Any]:
        """把显著检验结果推送给切片分析器，登记待计算切片（按维度注册表选默认维度值）。"""
        rows = [
            r for r in store.rows(self.MODULE)
            if r.get("实验编码") == exp_code and r.get("status") == "已检验"
        ]
        if not rows:
            return {"ok": False, "message": f"实验 {exp_code} 无已检验批次"}
        from app.domain import dimension_values, supported_dimensions
        dimension = supported_dimensions()[0]
        value = dimension_values(dimension)[0]
        slice_rows = store.rows("segment_slice")
        slice_rows.append({
            "id": max((int(r.get("id", 0)) for r in slice_rows), default=0) + 1,
            "切片批次": f"SG-{exp_code}-{datetime.now(timezone.utc).timestamp():.0f}",
            "实验编码": exp_code,
            "切片维度": dimension,
            "切片值": value,
            "分组差异": "",
            "status": "待计算",
            "pending": True,
            "abnormal": False,
            "数据版次": 1,
            "数据状态": "现行",
        })
        return {"ok": True, "message": f"切片批次已登记（{dimension}={value}，覆盖 {len(rows)} 条检验结果）"}

    def consume_from_metric_compute(self, metric_code: str) -> list[dict[str, Any]]:
        """读取指标计算层该指标的完成作业，返回可检验窗口。"""
        return [
            {"作业名称": r.get("作业名称", ""), "窗口水位": r.get("窗口水位", "")}
            for r in store.rows("metric_compute")
            if r.get("指标编码") == metric_code and r.get("status") == "已完成"
        ]

    def list_significant(self, exp_code: str) -> list[dict[str, Any]]:
        """列出实验的显著检验结果，供报告生成器写入结论。"""
        results = []
        for r in store.rows(self.MODULE):
            if r.get("实验编码") != exp_code or r.get("status") != "已检验":
                continue
            try:
                if float(r.get("P值", "1.0")) < 0.05:
                    results.append({
                        "指标编码": r.get("指标编码", ""),
                        "P值": r.get("P值", ""),
                        "置信区间": r.get("置信区间", ""),
                    })
            except ValueError:
                continue
        return results

    def sync_to_freshness(self, exp_code: str) -> dict[str, Any]:
        """同步检验批次完成时间到数据时效监控。"""
        rows = [r for r in store.rows(self.MODULE) if r.get("实验编码") == exp_code]
        if not rows:
            return {"ok": False, "message": "无检验批次"}
        latest = max(str(r.get("检验时间", "")) for r in rows) if rows else ""
        freshness_rows = store.rows("data_freshness")
        freshness_rows.append({
            "id": max((int(r.get("id", 0)) for r in freshness_rows), default=0) + 1,
            "监控对象": f"significance_test:{exp_code}",
            "数据域": "统计检验",
            "最新水位": latest or datetime.now(timezone.utc).isoformat(),
            "延迟分钟": "0",
            "时效等级": "新鲜",
            "status": "新鲜",
            "pending": True,
            "abnormal": False,
        })
        return {"ok": True, "message": "时效监控已同步"}
