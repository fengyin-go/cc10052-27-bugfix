"""切片分析器领域服务：管理实验差异的维度切片、切片值计算与失败重试。

数据链路位置：下钻分析层，消费分组分配器的权威分组与显著性检验的差异基线，
按维度/取值选样计算分组差异，计算确认后生成带版次的「报告切片凭证」，
报告生成器只能凭已确认凭证编排下钻章节。

关键口径：
- 选样严格按所选维度+取值过滤，不再回退成全实验计数，两个维度值各用各的分母；
- 已剔除样本（variant_assign.status == 已剔除）不计入任何分母；
- 缺对照组/实验组时判「证据不足」，保留不足依据，不输出 0% 差异；
- 维度更正推进实验数据版次，旧版次切片冻结可查，新版次重新计算确认后才可编排。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from app.domain import (
    EXCLUDED_STATUS,
    GROUP_CONTROL,
    GROUP_TREAT,
    current_revision,
    is_supported_slice,
    row_dimension_value,
    supported_dimensions,
    dimension_values,
)
from app.services.base import BaseModuleService
from app.store import store


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class SegmentSliceService(BaseModuleService):
    MODULE = "segment_slice"
    ENTITY = "切片结果"
    OPERATION_TERM = "计算"
    LIST_FIELDS = [
        "切片批次", "实验编码", "切片维度", "切片值", "分组差异",
        "数据版次", "数据状态", "切片凭证编码", "不足依据",
    ]
    REQUIRED_FIELDS = ["切片批次", "实验编码", "切片维度", "切片值"]
    STATUS_ORDER = ["待计算", "已计算", "计算失败"]
    # 动作先登记在规则表保证可识别，实际执行由 run_action 分派到领域方法
    ACTION_RULES = {"开始计算": "已计算", "重试计算": "待计算", "确认版本": "已确认"}
    NEGATIVE_ACTIONS = []

    # ---- 登记：校验维度与取值，落版次，杜绝错位选项 ----

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [
            field for field in self.REQUIRED_FIELDS
            if not str(values.get(field) or "").strip()
        ]
        if missing:
            return None, missing
        dimension = str(values.get("切片维度", "")).strip()
        value = str(values.get("切片值", "")).strip()
        if not is_supported_slice(dimension, value):
            return None, [
                f"切片维度/取值非法：{dimension}={value}，"
                f"支持 {', '.join(supported_dimensions())}"
            ]
        exp_code = str(values.get("实验编码", "")).strip()
        rows = store.rows(self.MODULE)
        entry: dict[str, Any] = {
            "id": max((int(row.get("id", 0)) for row in rows), default=0) + 1,
        }
        for field in self.LIST_FIELDS:
            entry[field] = values.get(field, "")
        entry.update({
            "status": "待计算",
            "pending": True,
            "abnormal": False,
            "数据版次": current_revision(exp_code),
            "对照组数": "",
            "实验组数": "",
            "有效样本数": "",
            "剔除样本数": "",
            "不足依据": "",
            "切片凭证编码": "",
            "数据状态": "现行",
        })
        rows.append(entry)
        return entry, []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return None, f"{self.ENTITY} {entry_id} 不存在或已归档"
        if action == "开始计算":
            result = self.run_slice(entry_id)
            return (entry, result["message"]) if result.get("ok") else (None, result["message"])
        if action == "重试计算":
            result = self.retry_failed(entry_id)
            return (entry, result["message"]) if result.get("ok") else (None, result["message"])
        if action == "确认版本":
            result = self.confirm_version(entry_id)
            return (entry, result["message"]) if result.get("ok") else (None, result["message"])
        return None, f"动作「{action}」不属于{self.MODULE}可执行范围"

    # ---- 业务扩展：维度选样、差异计算与凭证生成 ----

    def _select_samples(self, exp_code: str, dimension: str, value: str) -> list[dict[str, Any]]:
        """按实验+维度+取值选样，只取未剔除且维度值精确匹配的分组行。"""
        return [
            row for row in store.rows("variant_assign")
            if str(row.get("实验编码", "")) == exp_code
            and row.get("status") != EXCLUDED_STATUS
            and row_dimension_value(row, dimension) == value
        ]

    def _excluded_count(self, exp_code: str, dimension: str, value: str) -> int:
        """同维度值下被剔除的样本数，单独披露且不进分母。"""
        return sum(
            1 for row in store.rows("variant_assign")
            if str(row.get("实验编码", "")) == exp_code
            and row.get("status") == EXCLUDED_STATUS
            and row_dimension_value(row, dimension) == value
        )

    def run_slice(self, entry_id: int) -> dict[str, Any]:
        """执行切片计算：按所选维度/取值选样，独立分母计算分组差异并签发凭证。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return {"ok": False, "message": "切片不存在"}
        if entry.get("status") not in ("待计算", "计算失败"):
            return {"ok": False, "message": "仅待计算/计算失败切片可执行"}
        exp_code = str(entry.get("实验编码", ""))
        dimension = str(entry.get("切片维度", ""))
        value = str(entry.get("切片值", ""))
        if not is_supported_slice(dimension, value):
            entry["status"] = "计算失败"
            entry["abnormal"] = True
            entry["不足依据"] = f"不支持的切片维度/取值：{dimension}={value}"
            return {"ok": False, "message": f"切片维度 {dimension}={value} 不受支持"}

        revision = current_revision(exp_code)
        samples = self._select_samples(exp_code, dimension, value)
        excluded = self._excluded_count(exp_code, dimension, value)
        ctrl = sum(1 for row in samples if row.get("分组名称") == GROUP_CONTROL)
        treat = sum(1 for row in samples if row.get("分组名称") == GROUP_TREAT)
        effective = len(samples)

        entry["数据版次"] = revision
        entry["对照组数"] = ctrl
        entry["实验组数"] = treat
        entry["有效样本数"] = effective
        entry["剔除样本数"] = excluded
        entry["数据状态"] = "现行"

        # 缺组判据：无有效样本、缺对照组或缺实验组都不能算差异，
        # 必须保存不足依据，禁止把结果显示成 0% 差异。
        if effective == 0:
            reason = f"维度 {dimension}={value} 无有效样本（剔除 {excluded} 条不计入）"
        elif ctrl == 0:
            reason = f"维度 {dimension}={value} 缺对照组（实验组 {treat}、对照组 0），无法计算分组差异"
        elif treat == 0:
            reason = f"维度 {dimension}={value} 缺实验组（对照组 {ctrl}、实验组 0），无法计算分组差异"
        else:
            reason = ""
        if reason:
            entry["status"] = "计算失败"
            entry["pending"] = True
            entry["abnormal"] = True
            entry["不足依据"] = reason
            entry["分组差异"] = ""
            entry["切片凭证编码"] = ""
            return {"ok": False, "message": f"证据不足：{reason}"}

        # 分母只用当前切片自身的有效样本数，不同维度值不共用分母。
        diff = round((treat - ctrl) / effective, 4)
        credential = self._issue_credential(
            entry, revision=revision, ctrl=ctrl, treat=treat,
            effective=effective, excluded=excluded, diff=diff, samples=samples,
        )
        entry["分组差异"] = str(diff)
        entry["不足依据"] = ""
        entry["切片凭证编码"] = credential["切片凭证编码"]
        entry["status"] = "已计算"
        entry["pending"] = True
        entry["abnormal"] = False
        entry["计算时间"] = _now()
        return {
            "ok": True,
            "message": (
                f"切片 {dimension}={value}（版次 {revision}）："
                f"对照 {ctrl}/实验 {treat}/有效 {effective}/剔除 {excluded}，差异 {diff}"
            ),
            "entry": entry,
        }

    def _issue_credential(
        self, entry: dict[str, Any], *, revision: int, ctrl: int, treat: int,
        effective: int, excluded: int, diff: float, samples: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """生成报告切片凭证：完整快照冻结，报告据此识别细分范围与样本明细。"""
        exp_code = str(entry.get("实验编码", ""))
        dimension = str(entry.get("切片维度", ""))
        value = str(entry.get("切片值", ""))
        credential_rows = store.rows("slice_credential")
        credential = {
            "id": max((int(row.get("id", 0)) for row in credential_rows), default=0) + 1,
            "切片凭证编码": (
                f"SLC-{exp_code}-{dimension}-{value}-R{revision}-"
                f"{datetime.now(timezone.utc).timestamp():.0f}"
            ),
            "实验编码": exp_code,
            "切片批次": entry.get("切片批次", ""),
            "切片维度": dimension,
            "切片值": value,
            "数据版次": revision,
            "对照组数": ctrl,
            "实验组数": treat,
            "有效样本数": effective,
            "剔除样本数": excluded,
            "分组差异": diff,
            "样本明细": json.dumps([
                {
                    "用户标识": row.get("用户标识", ""),
                    "分组名称": row.get("分组名称", ""),
                    dimension: row_dimension_value(row, dimension),
                    "分配批次": row.get("分配批次", ""),
                }
                for row in samples
            ], ensure_ascii=False),
            "凭证状态": "已确认",
            "数据状态": "现行",
            "签发时间": _now(),
        }
        credential_rows.append(credential)
        return credential

    def retry_failed(self, entry_id: int) -> dict[str, Any]:
        """重试失败切片：清掉旧失败痕迹后回到待计算，维度非法时必须先改维度。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None or entry.get("status") != "计算失败":
            return {"ok": False, "message": "仅计算失败切片可重试"}
        dimension = str(entry.get("切片维度", ""))
        value = str(entry.get("切片值", ""))
        if not is_supported_slice(dimension, value):
            return {"ok": False, "message": "切片维度/取值仍非法，请先更正维度与取值再重试"}
        entry["status"] = "待计算"
        entry["pending"] = True
        entry["abnormal"] = False
        entry["不足依据"] = ""
        entry["分组差异"] = ""
        entry["重试次数"] = str(int(entry.get("重试次数", "0") or 0) + 1)
        return {"ok": True, "message": f"切片已回到待计算（第 {entry['重试次数']} 次重试）"}

    def confirm_version(self, entry_id: int) -> dict[str, Any]:
        """确认切片版次：确认后凭证才允许被报告集合领用。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return {"ok": False, "message": "切片不存在"}
        if entry.get("status") != "已计算" or entry.get("数据状态") != "现行":
            return {"ok": False, "message": "仅现行且已计算的切片可确认版次"}
        if not str(entry.get("切片凭证编码", "")).strip():
            return {"ok": False, "message": "切片缺少有效凭证，不能确认"}
        entry["status"] = "已确认"
        entry["pending"] = False
        # 同实验同维度同取值的旧确认版次让位为已过期，保证一个取值只有一个现行确认版次
        for row in store.rows(self.MODULE):
            if (
                row is not entry
                and row.get("实验编码") == entry.get("实验编码")
                and row.get("切片维度") == entry.get("切片维度")
                and row.get("切片值") == entry.get("切片值")
                and row.get("status") == "已确认"
            ):
                row["数据状态"] = "已过期"
                row["pending"] = False
        return {"ok": True, "message": f"切片 {entry.get('切片维度')}={entry.get('切片值')} 版次已确认"}

    # ---- 数据版次：维度更正联动，旧切片冻结 ----

    def mark_stale_for_revision(self, exp_code: str, new_revision: int) -> dict[str, Any]:
        """分组更正推进版次后，把该实验旧版次的现行切片整体冻结。

        切片行回到待计算等待按新版次重算，同时保留已过期标记与旧差异值，
        列表里可核对但不能再被当作完整证据；冻结留痕由凭证与集合表承载。
        """
        stale = 0
        for row in store.rows(self.MODULE):
            if (
                row.get("实验编码") == exp_code
                and row.get("数据状态") == "现行"
                and int(row.get("数据版次", 1) or 1) < new_revision
            ):
                row["数据状态"] = "已过期"
                row["status"] = "待计算"
                row["pending"] = True
                row["abnormal"] = False
                stale += 1
        for credential in store.rows("slice_credential"):
            if (
                credential.get("实验编码") == exp_code
                and credential.get("数据状态") == "现行"
                and int(credential.get("数据版次", 1) or 1) < new_revision
            ):
                credential["数据状态"] = "已过期"
        for slice_set in store.rows("slice_set"):
            if (
                slice_set.get("实验编码") == exp_code
                and slice_set.get("数据状态") == "现行"
                and int(slice_set.get("数据版次", 1) or 1) < new_revision
            ):
                slice_set["数据状态"] = "已过期"
        return {"ok": True, "message": f"实验 {exp_code} 已冻结 {stale} 条旧版次切片", "stale": stale}

    # ---- 切片集合：同次报告只能采用一个已确认版本 ----

    def build_slice_set(self, exp_code: str) -> dict[str, Any]:
        """把实验各维度取值的现行确认凭证汇成相容切片集合（同版次）。"""
        confirmed = [
            row for row in store.rows(self.MODULE)
            if row.get("实验编码") == exp_code
            and row.get("status") == "已确认"
            and row.get("数据状态") == "现行"
        ]
        if not confirmed:
            return {"ok": False, "message": "实验暂无已确认的现行切片，无法生成切片集合"}
        revisions = {int(row.get("数据版次", 1) or 1) for row in confirmed}
        if len(revisions) != 1:
            return {
                "ok": False,
                "message": f"已确认切片版次不相容（{sorted(revisions)}），请按最新版次重新计算确认",
            }
        # 同一维度的同一取值只接受一个确认版本；同一维度的不同取值（mobile/desktop）
        # 必须同时纳入集合，这是切片下钻的正常形态。
        seen: set[tuple[str, str]] = set()
        for row in confirmed:
            pair = (str(row.get("切片维度", "")), str(row.get("切片值", "")))
            if pair in seen:
                return {"ok": False, "message": f"切片 {pair[0]}={pair[1]} 存在多个已确认版本，请先确认唯一版次"}
            seen.add(pair)
        revision = revisions.pop()
        set_rows = store.rows("slice_set")
        credential_codes = [str(row.get("切片凭证编码", "")) for row in confirmed]
        slice_set = {
            "id": max((int(row.get("id", 0)) for row in set_rows), default=0) + 1,
            "切片集合编码": f"SLSET-{exp_code}-R{revision}-{datetime.now(timezone.utc).timestamp():.0f}",
            "实验编码": exp_code,
            "数据版次": revision,
            "切片凭证编码": json.dumps(credential_codes, ensure_ascii=False),
            "维度覆盖": json.dumps(
                [{"切片维度": row.get("切片维度", ""), "切片值": row.get("切片值", "")}
                 for row in confirmed],
                ensure_ascii=False,
            ),
            "数据状态": "现行",
            "确认时间": _now(),
        }
        # 同实验旧集合让位，一份实验同一时刻只允许一个现行集合
        for row in set_rows:
            if row.get("实验编码") == exp_code and row.get("数据状态") == "现行":
                row["数据状态"] = "已过期"
        set_rows.append(slice_set)
        return {
            "ok": True,
            "message": f"切片集合 {slice_set['切片集合编码']} 已确认（版次 {revision}，覆盖 {len(confirmed)} 个切片）",
            "slice_set": slice_set,
        }

    def list_slice_sets(self, exp_code: str) -> list[dict[str, Any]]:
        return [
            row for row in store.rows("slice_set")
            if row.get("实验编码") == exp_code
        ]

    def list_credentials(self, exp_code: str) -> list[dict[str, Any]]:
        return [
            row for row in store.rows("slice_credential")
            if row.get("实验编码") == exp_code
        ]

    def get_credential(self, credential_code: str) -> dict[str, Any] | None:
        return next(
            (row for row in store.rows("slice_credential")
             if row.get("切片凭证编码") == credential_code),
            None,
        )

    def credential_samples(self, credential_code: str) -> dict[str, Any]:
        """下钻回查：按凭证回查样本明细与对应维度。"""
        credential = self.get_credential(credential_code)
        if credential is None:
            return {"ok": False, "message": f"凭证 {credential_code} 不存在"}
        try:
            samples = json.loads(str(credential.get("样本明细", "[]")))
        except json.JSONDecodeError:
            samples = []
        return {
            "ok": True,
            "凭证": {key: value for key, value in credential.items() if key != "样本明细"},
            "样本明细": samples,
        }

    def batch_run_pending(self) -> dict[str, Any]:
        """批量执行全部待计算切片。"""
        pending = [r for r in store.rows(self.MODULE) if r.get("status") == "待计算"]
        done = sum(1 for r in pending if self.run_slice(int(r.get("id", 0))).get("ok"))
        return {"ok": True, "message": f"批量切片完成 {done}/{len(pending)}"}

    def consume_from_significance_test(self, exp_code: str) -> list[dict[str, Any]]:
        """读取显著性检验的置信区间，作为切片差异对照基线。"""
        return [
            {"指标编码": r.get("指标编码", ""), "置信区间": r.get("置信区间", "")}
            for r in store.rows("significance_test")
            if r.get("实验编码") == exp_code and r.get("status") == "已检验"
        ]

    def consume_from_cohort_builder(self, cohort_code: str) -> dict[str, Any]:
        """读取人群圈选器的人群包信息，限定切片范围。"""
        cohort = next((r for r in store.rows("cohort_builder") if r.get("人群编码") == cohort_code), None)
        if cohort is None:
            return {"ok": False, "message": f"人群包 {cohort_code} 不存在"}
        return {
            "ok": cohort.get("status") == "已生成",
            "人群规模": cohort.get("人群规模", ""),
            "状态": cohort.get("status", ""),
        }

    def produce_to_report_gen(self, exp_code: str) -> dict[str, Any]:
        """把现行确认切片集合推送给报告生成器，登记下钻章节报告。"""
        current_set = next(
            (row for row in reversed(store.rows("slice_set"))
             if row.get("实验编码") == exp_code and row.get("数据状态") == "现行"),
            None,
        )
        if current_set is None:
            return {"ok": False, "message": "实验无现行确认切片集合，请先计算确认并生成集合"}
        report_rows = store.rows("report_gen")
        report_rows.append({
            "id": max((int(r.get("id", 0)) for r in report_rows), default=0) + 1,
            "报告编码": f"RPT-SG-{exp_code}-{datetime.now(timezone.utc).timestamp():.0f}",
            "实验编码": exp_code,
            "报告模板": "切片下钻模板",
            "数据窗口": "最近7天",
            "生成状态": "排队中",
            "status": "排队中",
            "pending": True,
            "abnormal": False,
            "切片集合编码": current_set.get("切片集合编码", ""),
            "切片版次": current_set.get("数据版次", 1),
            "下钻章节": "[]",
        })
        return {"ok": True, "message": f"下钻报告已排队，领用集合 {current_set.get('切片集合编码')}"}

    def export_slice_matrix(self, exp_code: str) -> list[dict[str, Any]]:
        """导出实验的切片矩阵，供指标查询服务提供下钻数据。"""
        return [
            {
                "切片维度": r.get("切片维度", ""),
                "切片值": r.get("切片值", ""),
                "分组差异": r.get("分组差异", ""),
                "数据版次": r.get("数据版次", ""),
                "数据状态": r.get("数据状态", ""),
                "不足依据": r.get("不足依据", ""),
            }
            for r in store.rows(self.MODULE)
            if r.get("实验编码") == exp_code and r.get("status") in ("已计算", "已确认")
        ]

    def dimension_catalog(self) -> dict[str, Any]:
        """返回维度注册表及各维度取值，供前端渲染选项。"""
        return {
            "dimensions": [
                {"dimension": dimension, "values": dimension_values(dimension)}
                for dimension in supported_dimensions()
            ]
        }
