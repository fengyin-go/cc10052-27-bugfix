"""切片分析器 / 报告生成器端到端口径验证（无第三方依赖，直接断言领域服务）。

覆盖题目要求：
1. mobile/desktop 各自选维度取值后计算，分组结果不得回退成全实验计数，两维度不错位；
2. 计算前后在同一位置确认所选维度、取值和实际有效组别；
3. 已剔除样本不计入分母，不同维度值不共用分母；
4. 缺对照组保存不足依据，不显示 0% 差异；
5. 计算确认后生成报告切片凭证，报告按凭证识别细分范围；
6. 同次报告只采用一个已确认切片版本，旧报告可按冻结依据复算；
7. 维度更正与报告生成冲突：旧集合冻结、生成失败、新报告用相容新版次；
8. 下钻入口领取差异数据并回查样本明细与维度。
"""
from __future__ import annotations

import json
import sys

from app.services.report_gen import ReportGenService
from app.services.segment_slice import SegmentSliceService
from app.services.variant_assign import VariantAssignService
from app.store import store

FAILURES: list[str] = []


def check(condition: bool, label: str) -> None:
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {label}")
    if not condition:
        FAILURES.append(label)


def find_slice(exp_code: str, value: str):
    return next(
        row for row in store.rows("segment_slice")
        if row.get("实验编码") == exp_code and row.get("切片值") == value
        and row.get("切片维度") == "终端"
    )


def main() -> int:
    slice_svc = SegmentSliceService()
    report_svc = ReportGenService()
    assign_svc = VariantAssignService()

    # ---- 1. 选项提交：非法维度/取值必须被拒绝 ----
    bad_entry, errors = slice_svc.create_entry({
        "切片批次": "SG-BAD", "实验编码": "EXP-SLICE-01",
        "切片维度": "终端", "切片值": "tablet",
    })
    check(bad_entry is None and errors, "非法切片取值 tablet 被拒绝登记")

    # ---- 2. 计算 mobile / desktop，验证独立选样与独立分母 ----
    mobile = find_slice("EXP-SLICE-01", "mobile")  # 种子 id=4
    desktop = find_slice("EXP-SLICE-01", "desktop")  # 种子 id=5

    before_mobile = {k: mobile.get(k) for k in ("切片维度", "切片值", "数据版次", "status")}
    res_m = slice_svc.run_slice(int(mobile["id"]))
    check(res_m["ok"], f"mobile 切片计算成功：{res_m['message']}")
    check(before_mobile["切片维度"] == "终端" and before_mobile["切片值"] == "mobile",
          "计算前位置确认所选维度=终端、取值=mobile")
    check(mobile["切片维度"] == "终端" and mobile["切片值"] == "mobile",
          "计算后同一切片行仍承载维度=终端、取值=mobile（不错位）")
    check(int(mobile["对照组数"]) == 1 and int(mobile["实验组数"]) == 3,
          f"mobile 实际有效组别 对照1/实验3（实得 {mobile['对照组数']}/{mobile['实验组数']}）")
    check(int(mobile["有效样本数"]) == 4, "mobile 分母=4，仅本维度值有效样本")
    check(int(mobile["剔除样本数"]) == 1, "mobile 单独披露剔除 1 条（U-EX01）")
    check(float(mobile["分组差异"]) == round((3 - 1) / 4, 4),
          f"mobile 差异=(3-1)/4=0.5（实得 {mobile['分组差异']}）")

    res_d = slice_svc.run_slice(int(desktop["id"]))
    check(res_d["ok"], f"desktop 切片计算成功：{res_d['message']}")
    check(int(desktop["对照组数"]) == 3 and int(desktop["实验组数"]) == 1,
          f"desktop 实际有效组别 对照3/实验1（实得 {desktop['对照组数']}/{desktop['实验组数']}）")
    check(int(desktop["有效样本数"]) == 4, "desktop 分母=4，与 mobile 不共用分母")
    check(float(desktop["分组差异"]) == round((1 - 3) / 4, 4),
          f"desktop 差异=(1-3)/4=-0.5（实得 {desktop['分组差异']}）")
    check(mobile["分组差异"] != desktop["分组差异"],
          "两个维度值结果不同，未回退成全实验计数、未错位")

    # 全实验口径（含剔除前的错误算法）对照：8 个有效样本，(4-4)/8=0，证明旧 bug 已消除
    check(not (float(mobile["分组差异"]) == 0.0 and float(desktop["分组差异"]) == 0.0),
          "切片结果不再是全实验计数 0.0")

    # ---- 3. 报告切片凭证：含完整样本明细，可回查维度 ----
    cred_m = slice_svc.get_credential(str(mobile["切片凭证编码"]))
    check(cred_m is not None and cred_m["凭证状态"] == "已确认", "mobile 计算后签发报告切片凭证")
    samples_m = json.loads(cred_m["样本明细"])
    check(len(samples_m) == 4 and all(s["终端"] == "mobile" for s in samples_m),
          "凭证样本明细只含 mobile 成员且携带维度字段")
    check(all(s["用户标识"] != "U-EX01" for s in samples_m), "凭证样本不含已剔除成员 U-EX01")
    check(cred_m["剔除样本数"] == 1, "凭证记录剔除样本数但不并入分母")

    # ---- 4. 缺对照组：证据不足，禁止 0% 差异 ----
    no_ctrl = find_slice("EXP-SLICE-02", "mobile")  # 种子 id=6
    res_nc = slice_svc.run_slice(int(no_ctrl["id"]))
    check(not res_nc["ok"], "缺对照组切片计算被判定失败")
    check(no_ctrl["status"] == "计算失败" and no_ctrl["abnormal"],
          "缺组切片在列表中明确标记为计算失败/异常，不能被误选为完整证据")
    check("缺对照组" in str(no_ctrl["不足依据"]), f"保存不足依据：{no_ctrl['不足依据']}")
    check(no_ctrl["分组差异"] == "" and no_ctrl["切片凭证编码"] == "",
          "缺组切片不输出 0% 差异、不签发凭证")
    check(no_ctrl["实验组数"] == 3 and no_ctrl["对照组数"] == 0,
          "缺组切片仍披露真实组别计数（实验3/对照0）")

    # ---- 5. 确认版次并生成相容切片集合 ----
    check(not slice_svc.confirm_version(int(no_ctrl["id"]))["ok"],
          "证据不足的切片不能确认版本")
    check(slice_svc.confirm_version(int(mobile["id"]))["ok"], "mobile 版次确认成功")
    check(slice_svc.confirm_version(int(desktop["id"]))["ok"], "desktop 版次确认成功")
    check(mobile["status"] == "已确认" and desktop["status"] == "已确认",
          "两条切片均进入已确认状态")

    set_res = slice_svc.build_slice_set("EXP-SLICE-01")
    check(set_res["ok"], f"相容切片集合生成成功：{set_res['message']}")
    slice_set = set_res["slice_set"]
    check(int(slice_set["数据版次"]) == 1, "集合绑定数据版次 1")
    coverage = json.loads(slice_set["维度覆盖"])
    check({(c["切片维度"], c["切片值"]) for c in coverage}
          == {("终端", "mobile"), ("终端", "desktop")}, "集合覆盖 mobile 与 desktop 两个维度值")

    # 缺证据实验无法生成集合
    check(not slice_svc.build_slice_set("EXP-SLICE-02")["ok"],
          "无已确认切片的实验不能生成切片集合")

    # ---- 6. 报告领用凭证生成下钻章节 ----
    queue_res = slice_svc.produce_to_report_gen("EXP-SLICE-01")
    check(queue_res["ok"], "下钻报告凭现行集合排队成功")
    report = next(
        row for row in reversed(store.rows("report_gen"))
        if row.get("实验编码") == "EXP-SLICE-01" and row.get("切片集合编码") == slice_set["切片集合编码"]
    )
    check(report["切片集合编码"] == slice_set["切片集合编码"], "报告绑定唯一已确认切片集合")
    gen_res = report_svc.generate_report(int(report["id"]))
    check(gen_res["ok"], f"报告生成成功：{gen_res['message']}")
    chapter = json.loads(report["下钻章节"])
    check(len(chapter) == 2, "下钻章节领取两个切片的差异数据")
    chapter_by_value = {c["切片值"]: c for c in chapter}
    check(chapter_by_value["mobile"]["分组差异"] == 0.5
          and chapter_by_value["desktop"]["分组差异"] == -0.5,
          "下钻章节差异与切片凭证一致（0.5 / -0.5）")
    check(report["切片版次"] == 1 and json.loads(report["凭证快照"]),
          "报告冻结凭证快照与版次 1")

    # 下钻回查样本明细与维度
    drill = report_svc.drilldown_detail(int(report["id"]))
    check(drill["ok"], "下钻入口返回明细")
    flat_samples = [s for d in drill["下钻明细"] for s in d["样本回查"]]
    check(len(flat_samples) == 8 and all("终端" in s for s in flat_samples),
          "下钻报告可回查 8 条样本明细及对应维度")

    # ---- 7. 维度更正与报告生成冲突：mobile 成员更正到 desktop ----
    target = next(
        row for row in store.rows("variant_assign")
        if row.get("用户标识") == "U-M03" and row.get("实验编码") == "EXP-SLICE-01"
    )
    correct_res = assign_svc.correct_dimension(int(target["id"]), "终端", "desktop")
    check(correct_res["ok"], f"维度更正成功：{correct_res['message']}")
    check(correct_res["数据版次"] == 2, "更正后实验数据版次推进到 2")
    check(mobile["数据状态"] == "已过期" and desktop["数据状态"] == "已过期",
          "旧版次两条切片全部冻结")
    check(slice_set["数据状态"] == "已过期", "旧切片集合同步冻结，不能再作为完整证据")
    cred_m_after = slice_svc.get_credential(cred_m["切片凭证编码"])
    check(cred_m_after["数据状态"] == "已过期", "旧凭证标记过期但保留可查")

    # 已完成的旧报告仍可按冻结依据复算
    replay = report_svc.replay_report(int(report["id"]))
    check(replay["ok"], "旧报告复算入口可用")
    replay_mobile = next(c for c in replay["复算章节"] if c["切片值"] == "mobile")
    check(replay_mobile["复算差异"] == 0.5 and replay_mobile["复算分母"] == 4,
          "旧报告按版次1冻结依据复算（mobile 差异0.5/分母4），不受更正影响")

    # 生成中途冲突：新排队报告挂旧集合，生成必须失败而不是沿用旧证据
    queue2 = slice_svc.produce_to_report_gen("EXP-SLICE-01")
    # 现行集合已不存在（被冻结），produce 应拒绝；手工模拟旧报告排队后更正的场景
    conflict_report_row = {
        "id": max(int(r["id"]) for r in store.rows("report_gen")) + 1,
        "报告编码": "RPT-CONFLICT-01", "实验编码": "EXP-SLICE-01",
        "报告模板": "切片下钻模板", "数据窗口": "最近7天",
        "生成状态": "排队中", "status": "排队中", "pending": True, "abnormal": False,
        "切片集合编码": slice_set["切片集合编码"], "切片版次": 1, "下钻章节": "[]",
    }
    store.rows("report_gen").append(conflict_report_row)
    conflict_res = report_svc.generate_report(int(conflict_report_row["id"]))
    check(not conflict_res["ok"], "绑定冻结集合的报告生成被拦截")
    check(conflict_report_row["status"] == "生成失败"
          and "冻结" in str(conflict_report_row["失败原因"]),
          f"失败原因指明维度更正冲突：{conflict_report_row['失败原因']}")
    check(not report_svc.retry_failed(int(conflict_report_row["id"]))["ok"],
          "未改挂新集合前重试仍被拒绝")
    check(not queue2["ok"], "无现行集合时不允许排队新下钻报告")

    # ---- 8. 新版次重新计算确认，新报告使用相容两个维度版次 ----
    res_m2 = slice_svc.run_slice(int(mobile["id"]))
    check(res_m2["ok"] and int(mobile["数据版次"]) == 2, "mobile 按版次2重新计算")
    # 更正后：mobile 剩 实验2/对照1/共3（剔除仍不计）；desktop 实验2/对照3/共5
    check(int(mobile["实验组数"]) == 2 and int(mobile["对照组数"]) == 1
          and int(mobile["有效样本数"]) == 3,
          f"版次2 mobile 真实分组 实验2/对照1/分母3（实得 {mobile['实验组数']}/{mobile['对照组数']}/{mobile['有效样本数']}）")
    res_d2 = slice_svc.run_slice(int(desktop["id"]))
    check(res_d2["ok"] and int(desktop["数据版次"]) == 2, "desktop 按版次2重新计算")
    check(int(desktop["实验组数"]) == 2 and int(desktop["对照组数"]) == 3
          and int(desktop["有效样本数"]) == 5,
          f"版次2 desktop 真实分组 实验2/对照3/分母5（实得 {desktop['实验组数']}/{desktop['对照组数']}/{desktop['有效样本数']}）")
    check(float(mobile["分组差异"]) == round((2 - 1) / 3, 4)
          and float(desktop["分组差异"]) == round((2 - 3) / 5, 4),
          "版次2两维度差异按各自新分母重算")
    check(slice_svc.confirm_version(int(mobile["id"]))["ok"]
          and slice_svc.confirm_version(int(desktop["id"]))["ok"],
          "版次2两条切片重新确认")
    set2 = slice_svc.build_slice_set("EXP-SLICE-01")
    check(set2["ok"] and int(set2["slice_set"]["数据版次"]) == 2,
          "生成版次2相容切片集合")

    # 冲突报告改挂新集合后直接回到排队并生成成功
    rebind = report_svc.rebind_slice_set(
        int(conflict_report_row["id"]), set2["slice_set"]["切片集合编码"])
    check(rebind["ok"], "失败报告改挂版次2现行集合并回到排队")
    check(conflict_report_row["status"] == "排队中", "改挂后报告状态为排队中")
    gen2 = report_svc.generate_report(int(conflict_report_row["id"]))
    check(gen2["ok"], f"新报告按相容版次2生成成功：{gen2['message']}")
    chapter2 = {c["切片值"]: c for c in json.loads(conflict_report_row["下钻章节"])}
    check(chapter2["mobile"]["分组差异"] == round(1 / 3, 4)
          and chapter2["desktop"]["分组差异"] == -0.2,
          "新报告下钻数据来自版次2凭证")

    # 同实验新集合确认后，旧集合仍可查且保持已过期
    history_sets = slice_svc.list_slice_sets("EXP-SLICE-01")
    check(len(history_sets) == 2
          and {s["数据状态"] for s in history_sets} == {"现行", "已过期"},
          "切片集合历史保留：旧版次可查、新版次现行")

    print()
    if FAILURES:
        print(f"共 {len(FAILURES)} 项失败：")
        for label in FAILURES:
            print(f"  - {label}")
        return 1
    print("全部断言通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
