"""HTTP 端到端冒烟：经真实路由验证切片 -> 凭证 -> 集合 -> 报告 -> 更正冲突 -> 新版次全链路。

仅用标准库 urllib，启动方式：
  PYTHONPATH=. .venv/bin/python -m uvicorn app.main:app --port 8033 &
  python tests/http_smoke.py
"""
from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request

BASE = os.environ.get("SMOKE_BASE", "http://127.0.0.1:8033/api")
FAILURES: list[str] = []


def call(method: str, path: str, payload: dict | None = None):
    # 路径里可能含中文（如凭证编码中的维度名），逐段做 URL 编码；查询串原样保留
    pure_path, _, query = path.partition("?")
    quoted = "/".join(urllib.parse.quote(seg) for seg in pure_path.split("/"))
    if query:
        quoted = f"{quoted}?{query}"
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        BASE + quoted, data=data, method=method,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode())


def check(cond: bool, label: str) -> None:
    print(f"[{'PASS' if cond else 'FAIL'}] {label}")
    if not cond:
        FAILURES.append(label)


def find_slice(value: str):
    items = call("GET", "/segment_slice?size=200")["items"]
    return next(i for i in items
                if i["实验编码"] == "EXP-SLICE-01" and i["切片值"] == value)


def main() -> int:
    # 维度目录
    catalog = call("GET", "/segment_slice/dimensions/catalog")
    check(catalog["dimensions"][0]["values"] == ["mobile", "desktop"], "维度目录返回 mobile/desktop")

    # 两个维度值分别计算，结果独立
    mobile = find_slice("mobile")
    desktop = find_slice("desktop")
    rm = call("POST", f"/segment_slice/{mobile['id']}/run")
    rd = call("POST", f"/segment_slice/{desktop['id']}/run")
    check(rm["ok"] and rm["data"]["entry"]["分组差异"] == "0.5", "HTTP mobile 差异 0.5")
    check(rd["ok"] and rd["data"]["entry"]["分组差异"] == "-0.5", "HTTP desktop 差异 -0.5")

    # 缺对照组判证据不足
    no_ctrl = next(i for i in call("GET", "/segment_slice?size=200")["items"]
                   if i["实验编码"] == "EXP-SLICE-02")
    rnc = call("POST", f"/segment_slice/{no_ctrl['id']}/run")
    check(not rnc["ok"] and "缺对照组" in rnc["message"], "HTTP 缺对照组返回不足依据且无 0% 差异")

    # 确认 + 集合 + 凭证样本回查
    call("POST", f"/segment_slice/{mobile['id']}/confirm")
    call("POST", f"/segment_slice/{desktop['id']}/confirm")
    sset = call("POST", "/segment_slice/experiments/EXP-SLICE-01/slice-sets")["data"]["slice_set"]
    creds = call("GET", "/segment_slice/experiments/EXP-SLICE-01/credentials")["items"]
    check(len(creds) == 2, "两个切片凭证均可查")
    sample = call("GET", f"/segment_slice/credentials/{creds[0]['切片凭证编码']}/samples")
    check(sample["ok"] and len(sample["data"]["样本明细"]) == int(creds[0]["有效样本数"]),
          "凭证样本明细可回查且条数等于有效样本数")

    # 报告生成与下钻：排队两份报告，第一份完成，第二份留到更正后触发冲突
    call("POST", "/report_gen/experiments/EXP-SLICE-01/queue-drilldown")
    call("POST", "/report_gen/experiments/EXP-SLICE-01/queue-drilldown")
    reports = call("GET", "/report_gen?size=200")["items"]
    bound = [i for i in reports if i.get("切片集合编码") == sset["切片集合编码"]]
    report, conflict = bound[-2], bound[-1]
    conflict_id = conflict["id"]
    gen = call("POST", f"/report_gen/{report['id']}/actions", {"action": "开始生成"})
    check(gen["ok"] and gen["entry"]["status"] == "已完成", "HTTP 报告绑定集合生成完成")
    drill = call("GET", f"/report_gen/{report['id']}/drilldown")
    check(drill["ok"] and len(drill["data"]["下钻明细"]) == 2, "HTTP 下钻入口含两个切片")
    conflict_now = next(i for i in call("GET", "/report_gen?size=200")["items"]
                        if i["id"] == conflict_id)
    check(conflict_now["status"] == "排队中"
          and conflict_now["切片集合编码"] == sset["切片集合编码"],
          "第二份报告在更正前保持排队中且仍挂集合1")

    # 维度更正冲突
    member = next(i for i in call("GET", "/variant_assign/experiments/EXP-SLICE-01/members")["items"]
                  if i["用户标识"] == "U-M03")
    corr = call("POST", f"/variant_assign/{member['id']}/correct-dimension",
                {"维度": "终端", "新取值": "desktop"})
    check(corr["ok"] and corr["data"]["数据版次"] == 2, "HTTP 维度更正推进版次 2")
    stale = find_slice("mobile")
    check(stale["数据状态"] == "已过期" and stale["status"] == "待计算", "旧切片冻结并回到待计算")
    replay = call("POST", f"/report_gen/{report['id']}/replay")
    check(replay["ok"] and replay["data"]["复算章节"][0]["复算差异"]
          in (0.5, -0.5), "HTTP 旧报告按冻结凭证复算")

    # 生成中途冲突：排队中的报告绑定的集合已被更正冻结，生成必须失败
    rfail = call("POST", f"/report_gen/{conflict_id}/actions", {"action": "开始生成"})
    check(not rfail["ok"] and "冻结" in rfail["message"], "HTTP 生成中途更正冲突被拦截")
    conflict_failed = next(i for i in call("GET", "/report_gen?size=200")["items"]
                           if i["id"] == conflict_id)
    check(conflict_failed["status"] == "生成失败", "冲突报告落到生成失败状态")

    # 新版次重算 -> 新集合 -> 改挂 -> 生成成功
    call("POST", f"/segment_slice/{mobile['id']}/run")
    call("POST", f"/segment_slice/{desktop['id']}/run")
    call("POST", f"/segment_slice/{mobile['id']}/confirm")
    call("POST", f"/segment_slice/{desktop['id']}/confirm")
    sset2 = call("POST", "/segment_slice/experiments/EXP-SLICE-01/slice-sets")["data"]["slice_set"]
    check(int(sset2["数据版次"]) == 2, "HTTP 版次2相容集合生成")
    rebind = call("POST", f"/report_gen/{conflict_id}/rebind-slice-set",
                  {"切片集合编码": sset2["切片集合编码"]})
    check(rebind["ok"], "HTTP 冲突报告改挂版次2集合")
    gen2 = call("POST", f"/report_gen/{conflict_id}/actions", {"action": "开始生成"})
    check(gen2["ok"], "HTTP 新报告按相容版次生成成功")

    print()
    if FAILURES:
        print(f"{len(FAILURES)} 项失败：{FAILURES}")
        return 1
    print("HTTP 冒烟全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
