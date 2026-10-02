"""``python -m sensor_replay`` 命令行入口。

读取 UTF-8（可带 BOM）的温湿度样本（CSV 或 JSON Lines），按 timestamp_ms
升序以演示时钟立即回放：第一条样本的回放时间为零，其余样本按时间戳差值
推进。结果以 JSON Lines 逐行写入标准输出；任何输入错误都只写入标准错误并
以退出码 2 结束。

输入格式由 ``--format csv|jsonl`` 显式选择（默认 csv），不按文件扩展名推断。
可选的 ``--gap-threshold-ms MS`` 为每条输出记录增加布尔字段 missing_before，
标记当前记录与上一条输出记录的 timestamp_ms 差值是否严格大于阈值。
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
) -> str:
    """解析 CSV 文本并返回 JSON Lines 输出字符串；非法时抛出 InputError。

    start_ms/end_ms 为可选的 timestamp_ms 闭区间端点；省略的一端不限制。
    先校验全部记录（区间外的非法数据同样使输入失败），再按区间筛选。
    gap_threshold_ms 为可选的缺测标记阈值（毫秒，大于零的整数）。
    """
    reader = csv.reader(io.StringIO(text, newline=""))

    try:
        header = next(reader)
    except StopIteration:
        raise InputError("文件为空，缺少表头", 1)

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
    for record_no, row in enumerate(reader, start=2):
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

    return render_records(records, start_ms, end_ms, gap_threshold_ms)


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


def replay_jsonl_text(
    text: str,
    start_ms: int | None = None,
    end_ms: int | None = None,
    gap_threshold_ms: int | None = None,
) -> str:
    """解析 JSON Lines 文本并返回 JSON Lines 输出字符串。

    先逐行校验整个文件（区间外的非法记录同样使整次回放失败），再按时间
    区间筛选。物理行号从 1 开始且空白行也计数；忽略仅含空白的行；末行
    可以没有换行符。空文件或仅含空白行时正常返回空字符串。
    gap_threshold_ms 为可选的缺测标记阈值（毫秒，大于零的整数）。
    """
    records: list[tuple[int, int | float, int | float]] = []
    for line_no, line in enumerate(text.splitlines(), start=1):
        if not line.strip():  # 空白行计数但不参与解析
            continue
        records.append(parse_jsonl_line(line, line_no))

    return render_records(records, start_ms, end_ms, gap_threshold_ms)


def render_records(
    records: list[tuple[int, int | float, int | float]],
    start_ms: int | None,
    end_ms: int | None,
    gap_threshold_ms: int | None = None,
) -> str:
    """区间筛选、稳定排序并序列化为 JSON Lines；无命中时返回空字符串。

    gap_threshold_ms 不为 None 时，每条输出在原有四个字段之外追加布尔
    字段 missing_before：当前记录与上一条输出记录的 timestamp_ms 差值
    严格大于阈值时为 true；首条记录、差值恰好等于阈值及重复时间戳均为
    false。判定只依据筛选并排序后的相邻输出记录，区间外样本不参与。
    """
    # 按时间区间筛选（闭区间，两端包含）；只选原始样本，不插值。
    if start_ms is not None:
        records = [r for r in records if r[0] >= start_ms]
    if end_ms is not None:
        records = [r for r in records if r[0] <= end_ms]

    # 没有样本落入区间（或输入不含任何记录）：正常结束，标准输出为空。
    if not records:
        return ""

    # 稳定排序：时间戳升序；重复时间戳保留源文件中的先后顺序。
    records.sort(key=lambda item: item[0])
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
            line["missing_before"] = (
                previous_timestamp is not None
                and timestamp_ms - previous_timestamp > gap_threshold_ms
            )
        output.write(json.dumps(line, separators=(",", ":")))
        output.write("\n")
        previous_timestamp = timestamp_ms
    return output.getvalue()


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
            "可选的采样间隔缺测标记阈值（毫秒）：为每条输出追加布尔字段 "
            "missing_before，当前记录与上一条输出记录的 timestamp_ms 差值"
            "严格大于 MS 时为 true，首条记录固定为 false。仅接受大于零的"
            "十进制整数（允许前导零）"
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
    """校验 --gap-threshold-ms 取值：仅 ASCII 数字组成且大于零的十进制整数。"""
    if not _TIMESTAMP_RE.match(token):
        raise InputError(
            "--gap-threshold-ms 参数非法（需大于零的十进制整数，不接受符号、"
            f"小数、指数或空白）: {token!r}"
        )
    value = int(token)
    if value == 0:
        raise InputError(
            "--gap-threshold-ms 参数非法（需大于零的十进制整数，"
            f"不接受零）: {token!r}"
        )
    return value


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
        gap_threshold_ms = (
            parse_gap_threshold(args.gap_threshold_ms)
            if args.gap_threshold_ms is not None
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
                text, start_ms, end_ms, gap_threshold_ms
            )
        else:
            output = replay_csv_text(
                text, start_ms, end_ms, gap_threshold_ms
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
