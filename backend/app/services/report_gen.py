"""报告生成器领域服务：管理实验分析报告的模板编排、数据窗口聚合与生成重试。

数据链路位置：报告生产层，消费归因分析、显著性检验结论与切片分析器的已确认
切片凭证，按模板聚合成分析报告，并把成品交给报告分发推送给业务接收方。

切片相关口径：
- 同一份报告只能绑定一个已确认的切片集合（即一个版次下各维度取值的唯一确认版本）；
- 生成时把凭证快照冻结进报告，之后维度更正只影响新版次，旧报告仍可按冻结依据复算；
- 生成中途若发现绑定集合已被更正冻结，本次生成失败并提示改用相容新版次集合；
- 下钻章节携带切片差异与凭证编码，支持回查样本明细和对应维度。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from app.services.base import BaseModuleService
from app.services.segment_slice import SegmentSliceService
from app.store import store


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ReportGenService(BaseModuleService):
    MODULE = "report_gen"
    ENTITY = "分析报告"
    OPERATION_TERM = "校验"
    LIST_FIELDS = [
        "报告编码", "实验编码", "报告模板", "数据窗口", "生成状态",
        "切片集合编码", "切片版次",
    ]
    REQUIRED_FIELDS = ["报告编码", "实验编码", "报告模板"]
    STATUS_ORDER = ["排队中", "生成中", "已完成", "生成失败"]
    ACTION_RULES = {"开始生成": "生成中", "重试生成": "排队中"}
    NEGATIVE_ACTIONS = []

    # ---- 登记：保留切片集合挂接字段 ----

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [
            field for field in self.REQUIRED_FIELDS
            if not str(values.get(field) or "").strip()
        ]
        if missing:
            return None, missing
        rows = store.rows(self.MODULE)
        entry: dict[str, Any] = {
            "id": max((int(row.get("id", 0)) for row in rows), default=0) + 1,
        }
        for field in self.LIST_FIELDS:
            entry[field] = values.get(field, "")
        entry.update({
            "status": "排队中",
            "pending": True,
            "abnormal": False,
            "下钻章节": "[]",
        })
        rows.append(entry)
        return entry, []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return None, f"{self.ENTITY} {entry_id} 不存在或已归档"
        if action == "开始生成":
            result = self.generate_report(entry_id)
            if not result.get("ok"):
                # 透传领域失败原因（如维度更正导致切片集合冻结），不能只回通用话术
                return None, result["message"]
            return entry, result["message"]
        if action == "重试生成":
            result = self.retry_failed(entry_id)
            if not result.get("ok"):
                return None, result["message"]
            return entry, result["message"]
        return None, f"动作「{action}」不属于{self.MODULE}可执行范围"

    # ---- 业务扩展：报告编排与成品分发 ----

    def _load_slice_set(self, set_code: str) -> dict[str, Any] | None:
        return next(
            (row for row in store.rows("slice_set") if row.get("切片集合编码") == set_code),
            None,
        )

    def _build_drilldown_chapter(self, slice_set: dict[str, Any]) -> list[dict[str, Any]]:
        """领取切片差异数据编排下钻章节，冻结凭证编码与差异口径。"""
        slice_service = SegmentSliceService()
        credential_codes = json.loads(str(slice_set.get("切片凭证编码", "[]")))
        chapter: list[dict[str, Any]] = []
        for code in credential_codes:
            credential = slice_service.get_credential(code)
            if credential is None:
                continue
            chapter.append({
                "切片凭证编码": credential.get("切片凭证编码", ""),
                "切片维度": credential.get("切片维度", ""),
                "切片值": credential.get("切片值", ""),
                "数据版次": credential.get("数据版次", 1),
                "对照组数": credential.get("对照组数", 0),
                "实验组数": credential.get("实验组数", 0),
                "有效样本数": credential.get("有效样本数", 0),
                "剔除样本数": credential.get("剔除样本数", 0),
                "分组差异": credential.get("分组差异", 0),
            })
        return chapter

    def generate_report(self, entry_id: int) -> dict[str, Any]:
        """聚合检验与归因结论生成报告，完成后登记分发批次。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return {"ok": False, "message": "报告不存在"}
        if entry.get("status") not in ("排队中", "生成中"):
            return {"ok": False, "message": "仅排队中报告可生成"}
        exp_code = str(entry.get("实验编码", ""))
        significant = [
            r for r in store.rows("significance_test")
            if r.get("实验编码") == exp_code and r.get("status") == "已检验"
        ]
        attributed = [
            r for r in store.rows("attr_analysis")
            if r.get("实验编码") == exp_code and r.get("status") == "已归因"
        ]
        if not significant:
            entry["status"] = "生成失败"
            entry["生成状态"] = "生成失败"
            entry["abnormal"] = True
            entry["失败原因"] = "无已检验结论"
            return {"ok": False, "message": "实验无已检验结论，报告生成失败"}

        set_code = str(entry.get("切片集合编码", "") or "")
        drilldown_chapter: list[dict[str, Any]] = []
        if set_code:
            slice_set = self._load_slice_set(set_code)
            if slice_set is None:
                entry["status"] = "生成失败"
                entry["生成状态"] = "生成失败"
                entry["abnormal"] = True
                entry["失败原因"] = f"切片集合 {set_code} 不存在"
                return {"ok": False, "message": "绑定的切片集合不存在，报告生成失败"}
            # 生成中途的维度更正冲突：集合已冻结即不能作为现行证据
            if slice_set.get("数据状态") != "现行":
                entry["status"] = "生成失败"
                entry["生成状态"] = "生成失败"
                entry["abnormal"] = True
                entry["失败原因"] = (
                    f"切片集合 {set_code} 已随维度更正冻结（版次 {slice_set.get('数据版次')}），"
                    "请改用相容的新版次切片集合重新排队"
                )
                return {
                    "ok": False,
                    "message": "维度更正与报告生成冲突：绑定切片集合已冻结（已过期），请用相容新版次集合重新生成",
                }
            drilldown_chapter = self._build_drilldown_chapter(slice_set)
            if not drilldown_chapter:
                entry["status"] = "生成失败"
                entry["生成状态"] = "生成失败"
                entry["abnormal"] = True
                entry["失败原因"] = f"切片集合 {set_code} 下无有效凭证"
                return {"ok": False, "message": "切片集合缺少有效凭证，报告生成失败"}
            # 冻结凭证快照：旧报告此后只按冻结依据复算，不受后续更正影响
            entry["凭证快照"] = json.dumps(drilldown_chapter, ensure_ascii=False)
            entry["切片版次"] = slice_set.get("数据版次", 1)

        entry["status"] = "已完成"
        entry["生成状态"] = "已完成"
        entry["pending"] = False
        entry["abnormal"] = False
        entry["失败原因"] = ""
        entry["下钻章节"] = json.dumps(drilldown_chapter, ensure_ascii=False)
        entry["完成时间"] = _now()
        entry["结论条数"] = str(len(significant) + len(attributed))
        dist_rows = store.rows("report_dist")
        dist_rows.append({
            "id": max((int(r.get("id", 0)) for r in dist_rows), default=0) + 1,
            "分发批次": f"RD-{entry_id}-{datetime.now(timezone.utc).timestamp():.0f}",
            "报告编码": entry.get("报告编码", ""),
            "接收方": "实验负责人",
            "分发渠道": "站内信",
            "送达状态": "排队中",
            "status": "排队中",
            "pending": True,
            "abnormal": False,
        })
        scope = f"，含下钻切片 {len(drilldown_chapter)} 个" if drilldown_chapter else ""
        return {"ok": True, "message": f"报告已生成（结论 {entry['结论条数']} 条{scope}）并排队分发"}

    def retry_failed(self, entry_id: int) -> dict[str, Any]:
        """重试生成失败报告，重新校验数据依赖后回到排队。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None or entry.get("status") != "生成失败":
            return {"ok": False, "message": "仅生成失败报告可重试"}
        exp_code = str(entry.get("实验编码", ""))
        has_result = any(
            r.get("实验编码") == exp_code and r.get("status") == "已检验"
            for r in store.rows("significance_test")
        )
        if not has_result:
            return {"ok": False, "message": "依赖的检验结论仍未就绪，暂不能重试"}
        set_code = str(entry.get("切片集合编码", "") or "")
        if set_code:
            slice_set = self._load_slice_set(set_code)
            if slice_set is None or slice_set.get("数据状态") != "现行":
                return {
                    "ok": False,
                    "message": "绑定的切片集合已冻结或缺失，请改挂现行切片集合后再重试",
                }
        entry["status"] = "排队中"
        entry["生成状态"] = "排队中"
        entry["pending"] = True
        entry["abnormal"] = False
        entry["失败原因"] = ""
        entry["重试次数"] = str(int(entry.get("重试次数", "0") or 0) + 1)
        return {"ok": True, "message": f"报告已回到排队（第 {entry['重试次数']} 次重试）"}

    def rebind_slice_set(self, entry_id: int, slice_set_code: str) -> dict[str, Any]:
        """排队中/生成失败的报告改挂现行切片集合（维度更正后用相容新版次重新编排）。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return {"ok": False, "message": "报告不存在"}
        if entry.get("status") not in ("排队中", "生成失败"):
            return {"ok": False, "message": "仅排队中或生成失败报告可改挂切片集合"}
        slice_set = self._load_slice_set(slice_set_code)
        if slice_set is None:
            return {"ok": False, "message": f"切片集合 {slice_set_code} 不存在"}
        if slice_set.get("数据状态") != "现行":
            return {"ok": False, "message": "切片集合已冻结，不能挂接到新报告"}
        if str(slice_set.get("实验编码", "")) != str(entry.get("实验编码", "")):
            return {"ok": False, "message": "切片集合与报告实验不一致"}
        entry["切片集合编码"] = slice_set_code
        entry["切片版次"] = slice_set.get("数据版次", 1)
        entry["凭证快照"] = ""
        entry["下钻章节"] = "[]"
        # 改挂相容新版次后直接回到排队，可立即重新生成
        entry["status"] = "排队中"
        entry["生成状态"] = "排队中"
        entry["pending"] = True
        entry["abnormal"] = False
        entry["失败原因"] = ""
        return {"ok": True, "message": f"报告已改挂切片集合 {slice_set_code} 并回到排队"}

    def batch_generate(self) -> dict[str, Any]:
        """批量生成全部排队中报告。"""
        queued = [r for r in store.rows(self.MODULE) if r.get("status") == "排队中"]
        done, failed = 0, 0
        for row in queued:
            if self.generate_report(int(row.get("id", 0))).get("ok"):
                done += 1
            else:
                failed += 1
        return {"ok": True, "message": f"批量生成完成：成功 {done}，失败 {failed}"}

    def consume_from_attr_analysis(self, exp_code: str) -> list[dict[str, Any]]:
        """读取归因分析层的因子权重，编排报告归因章节。"""
        return [
            {"贡献因子": r.get("贡献因子", ""), "贡献权重": r.get("贡献权重", "")}
            for r in store.rows("attr_analysis")
            if r.get("实验编码") == exp_code and r.get("status") == "已归因"
        ]

    def consume_from_exp_design(self, exp_code: str) -> dict[str, Any]:
        """读取实验设计层方案信息，写入报告背景章节。"""
        exp = next((r for r in store.rows("exp_design") if r.get("实验编码") == exp_code), None)
        if exp is None:
            return {"ok": False, "message": f"实验方案 {exp_code} 不存在"}
        return {
            "ok": True,
            "实验名称": exp.get("实验名称", ""),
            "分组策略": exp.get("分组策略", ""),
            "状态": exp.get("status", ""),
        }

    def compose_summary(self, entry_id: int) -> dict[str, Any]:
        """合成报告摘要文本，包含显著结论、归因因子与下钻切片数量。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return {"ok": False, "message": "报告不存在"}
        exp_code = str(entry.get("实验编码", ""))
        sig_count = sum(
            1 for r in store.rows("significance_test")
            if r.get("实验编码") == exp_code and r.get("status") == "已检验"
        )
        attr_count = sum(
            1 for r in store.rows("attr_analysis")
            if r.get("实验编码") == exp_code and r.get("status") == "已归因"
        )
        try:
            chapter = json.loads(str(entry.get("下钻章节", "[]") or "[]"))
        except json.JSONDecodeError:
            chapter = []
        return {
            "ok": True,
            "摘要": (
                f"实验 {exp_code} 共 {sig_count} 条显著结论、{attr_count} 个归因因子、"
                f"{len(chapter)} 个下钻切片（版次 {entry.get('切片版次', '—')}）"
            ),
            "数据窗口": entry.get("数据窗口", ""),
        }

    def replay_report(self, entry_id: int) -> dict[str, Any]:
        """按报告冻结的凭证快照复算旧报告，不引用报告完成后的任何更正数据。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return {"ok": False, "message": "报告不存在"}
        if entry.get("status") != "已完成":
            return {"ok": False, "message": "仅已完成报告可按确认依据复算"}
        try:
            snapshot = json.loads(str(entry.get("凭证快照", "[]") or "[]"))
        except json.JSONDecodeError:
            snapshot = []
        recomputed = [
            {
                **item,
                "复算差异": item.get("分组差异"),
                "复算分母": item.get("有效样本数"),
            }
            for item in snapshot
        ]
        return {
            "ok": True,
            "报告编码": entry.get("报告编码", ""),
            "切片集合编码": entry.get("切片集合编码", ""),
            "切片版次": entry.get("切片版次", ""),
            "完成时间": entry.get("完成时间", ""),
            "复算章节": recomputed,
            "message": "已按报告冻结的切片凭证复算，未采用后续更正数据",
        }

    def drilldown_detail(self, entry_id: int) -> dict[str, Any]:
        """下钻入口：返回报告下钻章节，并支持按凭证回查样本明细与维度。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return {"ok": False, "message": "报告不存在"}
        try:
            chapter = json.loads(str(entry.get("下钻章节", "[]") or "[]"))
        except json.JSONDecodeError:
            chapter = []
        if not chapter:
            return {"ok": False, "message": "报告暂无下钻切片数据"}
        slice_service = SegmentSliceService()
        details = []
        for item in chapter:
            credential_code = str(item.get("切片凭证编码", ""))
            sample_result = slice_service.credential_samples(credential_code)
            details.append({
                "切片": item,
                "样本回查": sample_result.get("样本明细", []) if sample_result.get("ok") else [],
            })
        return {
            "ok": True,
            "报告编码": entry.get("报告编码", ""),
            "切片版次": entry.get("切片版次", ""),
            "下钻明细": details,
        }

    def archive_finished(self, entry_id: int) -> dict[str, Any]:
        """把完成报告的结论写入实验归档库。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None or entry.get("status") != "已完成":
            return {"ok": False, "message": "仅已完成报告可归档"}
        archive_rows = store.rows("exp_archive")
        archive_rows.append({
            "id": max((int(r.get("id", 0)) for r in archive_rows), default=0) + 1,
            "实验编码": entry.get("实验编码", ""),
            "结束时间": datetime.now(timezone.utc).isoformat(),
            "结论摘要": (
                f"报告 {entry.get('报告编码', '')} 结论 {entry.get('结论条数', '0')} 条，"
                f"切片版次 {entry.get('切片版次', '—')}"
            ),
            "归档位置": f"report/{entry.get('报告编码', '')}",
            "保留等级": "标准",
            "status": "待归档",
            "pending": True,
            "abnormal": False,
        })
        return {"ok": True, "message": "报告结论已写入归档库"}
