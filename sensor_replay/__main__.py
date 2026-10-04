"""``python -m sensor_replay`` 命令行入口。

读取 UTF-8（可带 BOM）的温湿度样本（CSV 或 JSON Lines），按 timestamp_ms
升序以演示时钟立即回放：第一条样本的回放时间为零，其余样本按时间戳差值
推进。结果以 JSON Lines 逐行写入标准输出；任何输入错误都只写入标准错误并
以退出码 2 结束。

输入格式由 ``--format csv|jsonl`` 显式选择（默认 csv），不按文件扩展名推断。

可选的 ``--gap-threshold-ms MS`` 启用缺测标记：输出对象在原有四个字段之外
增加布尔字段 missing_before——当前记录与上一条**输出**记录的 timestamp_ms
差值严格大于 MS 时为 true；首条输出记录固定为 false，差值恰好等于 MS 与
重复时间戳均为 false。判定基于区间筛选并稳定排序后的相邻记录。

可选的 ``--duplicate-policy all|first|last``（默认 all）控制同一 timestamp_ms
多条样本的保留策略：all 保留全部；first/last 分别只保留该时间戳在源文件中
最先、最后出现的一条完整样本。重复依据为解析后的 timestamp_ms 数值（CSV 中
01500 与 1500 同组），策略在整文件校验并完成区间筛选后生效。

可选的 ``--min-interval-ms MS`` 启用最小时间间隔抽样：在闭区间筛选、稳定
排序与重复点策略确定的记录上，按时间升序保留时间戳组——最早的时间戳组始终
保留，其后仅当某组与上一个**保留**组的时间戳差值至少为 MS（恰好相等也保留）
时才整体保留该组；被跳过的组不改变比较基准，末组不足间隔时不强行保留。抽样
只选择既有完整记录，不生成时间戳、不插值、不平均，保留记录的温湿度不变；
elapsed_ms 从抽样后的首条记录重新计起，missing_before 比较抽样后的相邻输出
记录。省略时保持全部既有行为。

可选的无值开关 ``--summary`` 不回放，而是向标准输出写入唯一一个统计摘要
JSON 对象（末尾带换行）：sample_count 为区间筛选与重复点策略后的最终记录
计数；first_ms/last_ms 为最终记录的最早、最晚时间戳；duration_ms 为两者
之差；temperature/humidity 各为只含 min/max 的对象，取最终记录的真实样本
极值，不插值、不平均。无选中记录时数量与跨度为 0，时间戳与各通道极值为
null。``--summary`` 不接受任何取值（``--summary=任意值`` 同为输入错误）。
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import math
import re
import sys
from pathlib import Path

REQUIRED_COLUMNS = ("timestamp_ms", "temperature", "humidity")

# timestamp_ms：仅非负十进制整数，不接受符号、空白、小数点或下划线。
# 注意用 \A...\Z 而非 ^...$：$ 会容忍末尾一个换行符，导致 "1000\n" 被误接受。
_TIMESTAMP_RE = re.compile(r"\A[0-9]+\Z")
# temperature/humidity：有限数值，允许小数和负数（不含 NaN/无穷/空白/下划线）。
_NUMBER_RE = re.compile(
    r"\A-?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?\Z"
)
_INTEGER_FORM_RE = re.compile(r"\A-?[0-9]+\Z")

EXIT_OK = 0
EXIT_INPUT_ERROR = 2


class InputError(Exception):
    """输入内容不合法。

    CSV 错误给出 record（表头算第 1 条 CSV 记录）；JSONL 错误给出从 1 开始
    的物理行号 line（空白行也计数）。两者均为 None 时错误信息不带定位前缀。
    """

    def __init__(
        self,
        message: str,
        record: int | None = None,
        line: int | None = None,
    ):
        super().__init__(message)
        self.message = message
        self.record = record
        self.line = line


def parse_timestamp(token: str, record: int) -> int:
    if not _TIMESTAMP_RE.match(token):
        raise InputError(
            f"timestamp_ms 非法（需非负十进制整数）: {token!r}", record
        )
    return int(token)


def parse_number(token: str, column: str, record: int) -> int | float:
    if not _NUMBER_RE.match(token):
        raise InputError(f"{column} 缺失或无法解析: {token!r}", record)
    # 整数字面量保留整数形态，其余按浮点，忠实保留样本数值。
    if _INTEGER_FORM_RE.match(token):
        return int(token)
    value = float(token)
    if not math.isfinite(value):  # 拒绝溢出为无穷的指数写法（如 1e999）
        raise InputError(f"{column} 必须为有限数值: {token!r}", record)
    return value


def replay_csv_text(
    text: str,
    start_ms: int | None = None,
    end_ms: int | None = None,
    gap_threshold_ms: int | None = None,
    duplicate_policy: str = "all",
    summary: bool = False,
    min_interval_ms: int | None = None,
) -> str:
    """解析 CSV 文本并返回 JSON Lines 输出字符串；非法时抛出 InputError。

    start_ms/end_ms 为可选的 timestamp_ms 闭区间端点；省略的一端不限制。
    gap_threshold_ms 启用时为每条输出附加 missing_before 缺测标记（摘要
    模式不输出记录，该参数仅继续被校验而不影响摘要字段）。
    duplicate_policy 为 all/first/last 的重复时间戳保留策略。
    summary 为真时改为返回唯一的统计摘要 JSON 对象（末尾带换行）。
    min_interval_ms 启用时在区间筛选与重复点策略之后做最小间隔抽样。
    先校验全部记录（区间外的非法数据同样使输入失败），再按区间筛选。
    """
    reader = csv.reader(io.StringIO(text, newline=""))

    try:
        header = next(reader)
    except StopIteration:
        raise InputError("文件为空，缺少表头", 1)
    except csv.Error as exc:
        # 表头算第 1 条 CSV 记录；字段超长等 csv 模块错误同样属于输入错误，
        # 不允许以未捕获异常的形式泄漏出 Python 堆栈。
        raise InputError(f"CSV 解析失败: {exc}", 1) from exc

    if len(header) != len(REQUIRED_COLUMNS) or set(header) != set(
        REQUIRED_COLUMNS
    ):
        raise InputError(
            "表头不合法：需恰好包含 timestamp_ms、temperature、humidity 三列，"
            "列顺序可变，不允许额外列或重复列",
            1,
        )

    ts_idx = header.index("timestamp_ms")
    temp_idx = header.index("temperature")
    hum_idx = header.index("humidity")

    records: list[tuple[int, int | float, int | float]] = []
    # 记录序号按逻辑 CSV 记录计数（表头为第 1 条、已在上方读出，故首条
    # 数据为第 2 条）；显式递增而不使用 reader.line_num——后者是物理行号，
    # 引号字段跨行时会大于记录序号。区间外或重复策略本会舍弃的记录同样
    # 经过此读取循环，字段超长等 csv.Error 仍使整个输入失败。
    row_iter = iter(reader)
    record_no = 2
    while True:
        try:
            row = next(row_iter)
        except StopIteration:
            break
        except csv.Error as exc:
            raise InputError(f"CSV 解析失败: {exc}", record_no) from exc
        if len(row) != len(REQUIRED_COLUMNS):
            raise InputError(
                f"数据行列数不符：应为 {len(REQUIRED_COLUMNS)} 列，"
                f"实际为 {len(row)} 列",
                record_no,
            )
        timestamp_ms = parse_timestamp(row[ts_idx], record_no)
        temperature = parse_number(row[temp_idx], "temperature", record_no)
        humidity = parse_number(row[hum_idx], "humidity", record_no)
        records.append((timestamp_ms, temperature, humidity))
        record_no += 1

    if summary:
        return render_summary(
            records, start_ms, end_ms, duplicate_policy, min_interval_ms
        )
    return render_records(
        records,
        start_ms,
        end_ms,
        gap_threshold_ms,
        duplicate_policy,
        min_interval_ms,
    )


# 向后兼容的别名：既有调用方使用 replay_text 表示 CSV 回放。
replay_text = replay_csv_text


# --- JSON Lines -----------------------------------------------------------

# json 解析钩子的数值标签：
# ("i", 原始整数字面量) / ("f", 原始浮点字面量) / ("c", NaN|Infinity 常量)。
# 必须借标签区分类型：json.loads 默认会把 1e3、1.0 读成 float、把 true 读成
# True（与整数 1 同属 int），无法在解析后再判别字面量形态。
_INT_TAG = "i"
_FLOAT_TAG = "f"
_CONST_TAG = "c"


def _json_type_name(value: object) -> str:
    """供错误信息使用的 JSON 类型名（布尔先于整数判断）。"""
    if isinstance(value, bool):
        return "布尔值"
    if value is None:
        return "null"
    if isinstance(value, str):
        return "字符串"
    if isinstance(value, list):
        return "数组"
    if isinstance(value, dict):
        return "对象"
    return "非数值类型"


def _jsonl_number(tag: object, field: str, line_no: int) -> int | float:
    """把温度/湿度字段的解析标签转换为有限 Python 数值，否则按行报错。"""
    if isinstance(tag, tuple) and tag[0] == _INT_TAG:
        return int(tag[1])
    if isinstance(tag, tuple) and tag[0] == _FLOAT_TAG:
        value = float(tag[1])
        if not math.isfinite(value):  # 1e999 等溢出为无穷的写法
            raise InputError(
                f"{field} 必须为有限数值（拒绝浮点溢出）: {tag[1]!r}",
                line=line_no,
            )
        return value
    if isinstance(tag, tuple) and tag[0] == _CONST_TAG:
        raise InputError(
            f"{field} 不允许 {tag[1]}（数值字段拒绝 NaN、Infinity 与 "
            "-Infinity）",
            line=line_no,
        )
    raise InputError(
        f"{field} 必须是有限 JSON 数值，不能是{_json_type_name(tag)}",
        line=line_no,
    )


def parse_jsonl_line(
    line: str, line_no: int
) -> tuple[int, int | float, int | float]:
    """解析并校验一行 JSONL，返回 (timestamp_ms, temperature, humidity)。"""

    def int_tag(raw: str) -> tuple[str, str]:
        return (_INT_TAG, raw)

    def float_tag(raw: str) -> tuple[str, str]:
        return (_FLOAT_TAG, raw)

    def constant_tag(raw: str) -> tuple[str, str]:
        # NaN / Infinity / -Infinity 不是 JSON 数值；交给字段校验报错，
        # 以便错误信息能指出具体字段。
        return (_CONST_TAG, raw)

    def pairs_hook(pairs: list[tuple[str, object]]) -> dict[str, object]:
        seen: set[str] = set()
        for key, _ in pairs:
            if key in seen:
                raise InputError(
                    f"JSON 对象存在重复键: {key!r}", line=line_no
                )
            seen.add(key)
        return dict(pairs)

    try:
        obj = json.loads(
            line,
            parse_int=int_tag,
            parse_float=float_tag,
            parse_constant=constant_tag,
            object_pairs_hook=pairs_hook,
        )
    except InputError:
        raise
    except json.JSONDecodeError as exc:
        raise InputError(f"JSON 语法错误: {exc.msg}", line=line_no)

    # 每个非空行必须恰好是一个 JSON 对象（而非数组、标量或 null）。
    if not isinstance(obj, dict):
        raise InputError(
            f"每一行必须是一个 JSON 对象，实际为{_json_type_name(obj)}",
            line=line_no,
        )

    missing = [key for key in REQUIRED_COLUMNS if key not in obj]
    extra = [key for key in obj if key not in REQUIRED_COLUMNS]
    if missing or extra:
        details = []
        if missing:
            details.append(f"缺少键: {', '.join(missing)}")
        if extra:
            details.append(f"多余键: {', '.join(extra)}")
        raise InputError(
            "JSON 对象必须且只能包含 timestamp_ms、temperature、humidity "
            f"三个键（{'；'.join(details)}）",
            line=line_no,
        )

    # timestamp_ms：只接受不带负号的 JSON 整数字面量；指数与小数在 JSON
    # 词法中属于浮点标签，负数整数通过原始字面量的前导 '-' 识别（含 -0）。
    ts_tag = obj["timestamp_ms"]
    if (
        not isinstance(ts_tag, tuple)
        or ts_tag[0] != _INT_TAG
        or ts_tag[1].startswith("-")
    ):
        raise InputError(
            "timestamp_ms 必须是不带负号的 JSON 整数字面量"
            "（拒绝负数、小数、指数、字符串、布尔值与 null）",
            line=line_no,
        )
    timestamp_ms = int(ts_tag[1])
    temperature = _jsonl_number(obj["temperature"], "temperature", line_no)
    humidity = _jsonl_number(obj["humidity"], "humidity", line_no)
    return timestamp_ms, temperature, humidity


# 物理行切分只认 LF、CRLF 与单独 CR（CRLF 只计一次换行）。不能用
# str.splitlines()：它还把 U+000B、U+0085、U+2028 等 Unicode 行边界当作
# 换行，会把同一物理行拆成多条样本；这些字符应作为所在行的内容参与校验。
_PHYSICAL_LINE_RE = re.compile(r"\r\n|\r|\n")


def split_physical_lines(text: str) -> list[str]:
    """按 LF / CRLF / 单独 CR 切分物理行，与 splitlines 一样不产生末尾空行。

    其他 Unicode 行分隔符（U+000B、U+0085、U+2028 等）不视为换行，
    原样保留在所在行内容中。
    """
    lines = _PHYSICAL_LINE_RE.split(text)
    # 末尾的换行符不额外产生一个空行（与 str.splitlines 行为一致）。
    if lines and lines[-1] == "" and text.endswith(("\n", "\r")):
        lines.pop()
    return lines


def replay_jsonl_text(
    text: str,
    start_ms: int | None = None,
    end_ms: int | None = None,
    gap_threshold_ms: int | None = None,
    duplicate_policy: str = "all",
    summary: bool = False,
    min_interval_ms: int | None = None,
) -> str:
    """解析 JSON Lines 文本并返回 JSON Lines 输出字符串。

    先逐行校验整个文件（区间外的非法记录同样使整次回放失败），再按时间
    区间筛选。物理行号从 1 开始且空白行也计数；忽略仅含空白的行；末行
    可以没有换行符。物理行只按 LF、CRLF 与单独 CR 切分（CRLF 计一次）；
    U+000B、U+0085、U+2028 等其他 Unicode 行边界不视为换行，作为所在行
    的内容参与校验。空文件或仅含空白行时正常返回空字符串；summary 为真
    时改为返回唯一的统计摘要 JSON 对象（末尾带换行），无记录时摘要中
    数量与跨度为 0、其余字段为 null。min_interval_ms 启用时在区间筛选与
    重复点策略之后做最小间隔抽样。
    """
    records: list[tuple[int, int | float, int | float]] = []
    for line_no, line in enumerate(split_physical_lines(text), start=1):
        if not line.strip():  # 空白行计数但不参与解析
            continue
        records.append(parse_jsonl_line(line, line_no))

    if summary:
        return render_summary(
            records, start_ms, end_ms, duplicate_policy, min_interval_ms
        )
    return render_records(
        records,
        start_ms,
        end_ms,
        gap_threshold_ms,
        duplicate_policy,
        min_interval_ms,
    )


def select_records(
    records: list[tuple[int, int | float, int | float]],
    start_ms: int | None,
    end_ms: int | None,
    duplicate_policy: str = "all",
) -> list[tuple[int, int | float, int | float]]:
    """闭区间筛选、稳定排序并按策略去重，返回最终参与输出的记录。

    顺序固定为：闭区间筛选 → 按 timestamp_ms 稳定排序（重复时间戳保持源
    文件先后）→ 按 duplicate_policy 去重（all 保留全部；first/last 分别只
    保留同一时间戳在源文件中最先/最后出现的一条完整样本，重复依据为解析
    后的 timestamp_ms 数值，且记录不必相邻）。
    """
    # 按时间区间筛选（闭区间，两端包含）；只选原始样本，不插值。
    if start_ms is not None:
        records = [r for r in records if r[0] >= start_ms]
    if end_ms is not None:
        records = [r for r in records if r[0] <= end_ms]

    if not records:
        return []

    # 稳定排序：时间戳升序；重复时间戳保留源文件中的先后顺序。
    records.sort(key=lambda item: item[0])

    if duplicate_policy != "all":
        # 排序后同一时间戳必然相邻；first 保留每组首条，last 保留末条，
        # 选中的温湿度始终来自同一条原始记录，不平均也不拼接。
        deduped: list[tuple[int, int | float, int | float]] = []
        index = 0
        while index < len(records):
            next_index = index + 1
            while (
                next_index < len(records)
                and records[next_index][0] == records[index][0]
            ):
                next_index += 1
            if duplicate_policy == "first":
                deduped.append(records[index])
            else:  # "last"
                deduped.append(records[next_index - 1])
            index = next_index
        records = deduped

    return records


def sample_min_interval(
    records: list[tuple[int, int | float, int | float]],
    min_interval_ms: int | None,
) -> list[tuple[int, int | float, int | float]]:
    """在已区间筛选、稳定排序并去重的记录上做最小时间间隔抽样。

    按时间升序遍历时间戳组（排序后同一时间戳的记录必然相邻，all 下一个
    组可含多条）：最早的组始终保留；其后仅当某组与上一个**保留**组的
    timestamp_ms 差值至少为 min_interval_ms（恰好相等也保留）时才整体
    保留该组。被跳过的组不改变比较基准；末组不足间隔时不强行保留。
    抽样只选择既有完整记录，不生成时间戳、不插值、不平均。
    min_interval_ms 为 None 时原样返回，保持全部既有行为。
    """
    if min_interval_ms is None or not records:
        return records

    kept: list[tuple[int, int | float, int | float]] = []
    index = 0
    total = len(records)
    while index < total:
        group_timestamp = records[index][0]
        next_index = index + 1
        while (
            next_index < total
            and records[next_index][0] == group_timestamp
        ):
            next_index += 1

        if (
            not kept
            or group_timestamp - kept[-1][0] >= min_interval_ms
        ):
            # 整组保留：all 维持组内源顺序，first/last 组内本就只有一条。
            kept.extend(records[index:next_index])
        index = next_index
    return kept


def render_records(
    records: list[tuple[int, int | float, int | float]],
    start_ms: int | None,
    end_ms: int | None,
    gap_threshold_ms: int | None = None,
    duplicate_policy: str = "all",
    min_interval_ms: int | None = None,
) -> str:
    """区间筛选、稳定排序、去重、抽样并序列化为 JSON Lines；无命中返回空串。

    先按闭区间筛选与重复点策略确定记录，再按 min_interval_ms 做最小间隔
    抽样。gap_threshold_ms 非 None 时为每条输出附加布尔字段
    missing_before：依据**抽样后**的相邻输出记录，当前记录与上一条输出
    记录的 timestamp_ms 差值严格大于阈值时为 true；首条记录固定为
    false，差值恰好等于阈值与重复时间戳均为 false。elapsed_ms 从抽样后
    的首条记录计起。
    """
    records = select_records(records, start_ms, end_ms, duplicate_policy)
    records = sample_min_interval(records, min_interval_ms)

    # 没有样本落入区间（或输入不含任何记录）：正常结束，标准输出为空。
    if not records:
        return ""

    first_timestamp = records[0][0]

    output = io.StringIO()
    previous_timestamp: int | None = None
    for timestamp_ms, temperature, humidity in records:
        line = {
            "timestamp_ms": timestamp_ms,
            "elapsed_ms": timestamp_ms - first_timestamp,
            "temperature": temperature,
            "humidity": humidity,
        }
        if gap_threshold_ms is not None:
            # 首条输出记录固定 false；严格大于才算缺测，等于阈值与重复
            # 时间戳（差值为 0）均为 false。
            line["missing_before"] = (
                previous_timestamp is not None
                and timestamp_ms - previous_timestamp > gap_threshold_ms
            )
        previous_timestamp = timestamp_ms
        output.write(json.dumps(line, separators=(",", ":")))
        output.write("\n")
    return output.getvalue()


def build_summary(
    records: list[tuple[int, int | float, int | float]],
    start_ms: int | None,
    end_ms: int | None,
    duplicate_policy: str = "all",
    min_interval_ms: int | None = None,
) -> dict[str, object]:
    """对区间筛选、去重与最小间隔抽样后的最终记录计算统计摘要。

    sample_count 为最终记录计数（all 下重复点分别计数，first/last 沿用
    源文件取完整样本的规则）；first_ms/last_ms 为最终记录最早、最晚时间
    戳；duration_ms 为两者之差（不取参数边界）；temperature/humidity 各
    为只含 min/max 的对象，取最终记录的真实样本极值，不插值、不平均。
    无选中记录时数量与跨度为 0，时间戳与各通道 min/max 均为 null。
    """
    records = select_records(records, start_ms, end_ms, duplicate_policy)
    records = sample_min_interval(records, min_interval_ms)
    if not records:
        return {
            "sample_count": 0,
            "first_ms": None,
            "last_ms": None,
            "duration_ms": 0,
            "temperature": {"min": None, "max": None},
            "humidity": {"min": None, "max": None},
        }

    first_ms = records[0][0]
    last_ms = records[-1][0]
    temperatures = [temperature for _, temperature, _ in records]
    humidities = [humidity for _, _, humidity in records]
    return {
        "sample_count": len(records),
        "first_ms": first_ms,
        "last_ms": last_ms,
        "duration_ms": last_ms - first_ms,
        "temperature": {"min": min(temperatures), "max": max(temperatures)},
        "humidity": {"min": min(humidities), "max": max(humidities)},
    }


def render_summary(
    records: list[tuple[int, int | float, int | float]],
    start_ms: int | None,
    end_ms: int | None,
    duplicate_policy: str = "all",
    min_interval_ms: int | None = None,
) -> str:
    """返回摘要 JSON 字符串：唯一一个 JSON 对象加末尾换行。"""
    summary = build_summary(
        records, start_ms, end_ms, duplicate_policy, min_interval_ms
    )
    return json.dumps(summary, separators=(",", ":")) + "\n"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sensor_replay",
        description=(
            "读取 UTF-8（可带 BOM）的温湿度样本，按 timestamp_ms 升序以演示"
            "时钟立即回放，逐行输出 JSON（timestamp_ms/elapsed_ms/"
            "temperature/humidity）。输入格式由 --format 选择，不按扩展名推断。"
        ),
    )
    parser.add_argument(
        "path",
        metavar="PATH",
        help="温湿度样本文件路径（UTF-8 编码，可带 BOM；路径可含空格）",
    )
    parser.add_argument(
        "--format",
        choices=("csv", "jsonl"),
        default="csv",
        help=(
            "输入文件格式：csv（默认）或 jsonl（JSON Lines，每行一个 JSON "
            "对象）。必须显式指定，不按文件扩展名推断"
        ),
    )
    parser.add_argument(
        "--start-ms",
        metavar="MS",
        default=None,
        help=(
            "只回放 timestamp_ms >= MS 的样本（下界，包含该端点）；"
            "省略表示不限制下界。仅接受非负十进制整数"
        ),
    )
    parser.add_argument(
        "--end-ms",
        metavar="MS",
        default=None,
        help=(
            "只回放 timestamp_ms <= MS 的样本（上界，包含该端点）；"
            "省略表示不限制上界。仅接受非负十进制整数"
        ),
    )
    parser.add_argument(
        "--gap-threshold-ms",
        metavar="MS",
        default=None,
        help=(
            "可选缺测标记阈值（正十进制整数，允许前导零）：启用后每条"
            "输出在原有四个字段之外增加布尔字段 missing_before——当前"
            "记录与上一条输出记录的 timestamp_ms 差值严格大于 MS 时为 "
            "true；首条输出记录固定为 false，差值恰好等于 MS 与重复"
            "时间戳均为 false。缺省则不输出该字段"
        ),
    )
    parser.add_argument(
        "--duplicate-policy",
        choices=("all", "first", "last"),
        default="all",
        help=(
            "重复时间戳（相同 timestamp_ms 数值）的保留策略，可选值为 "
            "all、first、last，默认 all。all 保留同一时间戳的全部样本"
            "（维持源文件先后顺序）；first 只保留该时间戳在源文件中最先"
            "出现的一条完整样本，last 只保留最后出现的一条（不平均、不"
            "拼接）。重复依据为解析后的 timestamp_ms 数值（如 CSV 中 "
            "01500 与 1500 视为相同时间戳），策略在整文件校验并完成区间"
            "筛选后生效"
        ),
    )
    parser.add_argument(
        "--min-interval-ms",
        metavar="MS",
        default=None,
        help=(
            "可选最小时间间隔抽样阈值（正十进制整数，允许前导零）：在闭"
            "区间筛选与重复点策略确定的记录上，按时间升序保留时间戳组——"
            "最早的时间戳组始终保留；其后某组与上一个保留组的 "
            "timestamp_ms 差值至少为 MS 时才整体保留（恰好相等也保留）；"
            "被跳过的组不改变比较基准，末组不足间隔时不强行保留。同一"
            "时间戳的记录整体保留或跳过；抽样不生成新时间戳、不插值、不"
            "平均，elapsed_ms 从抽样后的首条记录计起，missing_before 比较"
            "抽样后的相邻输出记录。省略时保持全部既有行为"
        ),
    )
    parser.add_argument(
        "--summary",
        action="store_true",
        default=False,
        help=(
            "可选无值开关：不回放，而是向标准输出写入唯一一个统计摘要"
            "JSON 对象（末尾带换行），含 sample_count、first_ms、last_ms、"
            "duration_ms 以及 temperature/humidity 各自的 min/max 共六个"
            "字段。计数与极值均基于整文件校验后再经区间筛选与重复点策略"
            "确定的最终记录；无选中记录时数量与跨度为 0，时间戳与各通道"
            "极值为 null。可与 --gap-threshold-ms 同时使用（阈值仍被校验"
            "但不影响摘要）；该开关不接受任何取值，--summary=任意值 均为"
            "输入错误"
        ),
    )
    return parser


def parse_bound(token: str, option: str) -> int:
    """校验 --start-ms/--end-ms 取值：仅 ASCII 数字组成的非负十进制整数。"""
    if not _TIMESTAMP_RE.match(token):
        raise InputError(
            f"{option} 参数非法（需非负十进制整数，不接受符号、小数、"
            f"指数或空白）: {token!r}"
        )
    return int(token)


def parse_gap_threshold(token: str) -> int:
    """校验 --gap-threshold-ms：仅 ASCII 数字组成且数值大于零。

    允许前导零；缺值、空字符串、零、符号、空白、小数、指数等写法一律拒绝。
    """
    option = "--gap-threshold-ms"
    if not _TIMESTAMP_RE.match(token) or int(token) <= 0:
        raise InputError(
            f"{option} 参数非法（需数值大于零的十进制整数，不接受零、"
            f"符号、小数、指数或空白）: {token!r}"
        )
    return int(token)


def parse_min_interval(token: str) -> int:
    """校验 --min-interval-ms：仅 ASCII 数字组成且数值大于零。

    允许前导零；缺值、空字符串、零、符号、空白、小数、指数或其他字符一律
    拒绝（与 --gap-threshold-ms 相同的取值规则）。
    """
    option = "--min-interval-ms"
    if not _TIMESTAMP_RE.match(token) or int(token) <= 0:
        raise InputError(
            f"{option} 参数非法（需数值大于零的十进制整数，不接受零、"
            f"符号、小数、指数或空白）: {token!r}"
        )
    return int(token)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    path = Path(args.path)

    try:
        start_ms = (
            parse_bound(args.start_ms, "--start-ms")
            if args.start_ms is not None
            else None
        )
        end_ms = (
            parse_bound(args.end_ms, "--end-ms")
            if args.end_ms is not None
            else None
        )
        if (
            start_ms is not None
            and end_ms is not None
            and start_ms > end_ms
        ):
            raise InputError(
                f"区间非法：--start-ms ({start_ms}) 大于 "
                f"--end-ms ({end_ms})，起点不能大于终点"
            )
        gap_threshold_ms = (
            parse_gap_threshold(args.gap_threshold_ms)
            if args.gap_threshold_ms is not None
            else None
        )
        min_interval_ms = (
            parse_min_interval(args.min_interval_ms)
            if args.min_interval_ms is not None
            else None
        )
    except InputError as exc:
        print(f"错误: {exc.message}", file=sys.stderr)
        return EXIT_INPUT_ERROR

    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        print(f"错误: 文件不存在: {args.path}", file=sys.stderr)
        return EXIT_INPUT_ERROR
    except IsADirectoryError:
        print(f"错误: 路径是目录而非文件: {args.path}", file=sys.stderr)
        return EXIT_INPUT_ERROR
    except OSError as exc:
        print(
            f"错误: 无法读取文件 {args.path}: {exc.strerror or exc}",
            file=sys.stderr,
        )
        return EXIT_INPUT_ERROR

    try:
        text = raw.decode("utf-8-sig")  # utf-8-sig 同时处理有无 BOM
    except UnicodeDecodeError:
        print(
            f"错误: 文件 {args.path} 不是有效的 UTF-8 编码",
            file=sys.stderr,
        )
        return EXIT_INPUT_ERROR

    try:
        if args.format == "jsonl":
            output = replay_jsonl_text(
                text,
                start_ms,
                end_ms,
                gap_threshold_ms,
                args.duplicate_policy,
                args.summary,
                min_interval_ms,
            )
        else:
            output = replay_csv_text(
                text,
                start_ms,
                end_ms,
                gap_threshold_ms,
                args.duplicate_policy,
                args.summary,
                min_interval_ms,
            )
    except InputError as exc:
        if exc.line is not None:
            location = f"第 {exc.line} 行: "
        elif exc.record is not None:
            location = f"第 {exc.record} 条 CSV 记录: "
        else:
            location = ""
        print(f"错误: {location}{exc.message}", file=sys.stderr)
        return EXIT_INPUT_ERROR

    sys.stdout.write(output)
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
