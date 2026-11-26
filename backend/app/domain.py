"""切片领域共享口径：维度注册表、分组常量与实验数据版次。

切片分析器按这里登记的维度做选样，分组分配器的维度更正通过版次号通知下游，
保证同一份实验数据在任意时刻只有一个现行版次，旧版次凭证冻结留痕。
"""
from __future__ import annotations

# 对照组 / 实验组是平台内统一的分组名称
GROUP_CONTROL = "对照组"
GROUP_TREAT = "实验组"
EXCLUDED_STATUS = "已剔除"

# 维度注册表：维度 -> 允许的取值。新增维度只需在此登记，
# 分组分配器行上保存同名字段（如「终端」=mobile/desktop）。
SLICE_DIMENSIONS: dict[str, dict[str, list[str]]] = {
    "终端": {"values": ["mobile", "desktop"]},
}

# 实验数据版次：维度更正一次，版次 +1，下游切片凭证据此判新旧。
EXP_DATA_REVISION: dict[str, int] = {}


def supported_dimensions() -> list[str]:
    return list(SLICE_DIMENSIONS.keys())


def dimension_values(dimension: str) -> list[str]:
    spec = SLICE_DIMENSIONS.get(dimension)
    return list(spec["values"]) if spec else []


def is_supported_slice(dimension: str, value: str) -> bool:
    return dimension in SLICE_DIMENSIONS and value in SLICE_DIMENSIONS[dimension]["values"]


def current_revision(exp_code: str) -> int:
    """读取实验当前数据版次，未发生过更正的实验默认第 1 版。"""
    return EXP_DATA_REVISION.setdefault(str(exp_code), 1)


def bump_revision(exp_code: str) -> int:
    """维度更正后推进版次，返回新版次号。"""
    exp_code = str(exp_code)
    EXP_DATA_REVISION[exp_code] = current_revision(exp_code) + 1
    return EXP_DATA_REVISION[exp_code]


def row_dimension_value(row: dict, dimension: str) -> str:
    """读取分组行上的维度取值，缺字段按空值处理，不允许回退成全实验口径。"""
    return str(row.get(dimension, "") or "")
