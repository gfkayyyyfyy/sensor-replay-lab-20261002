"""``python -m sensor_replay`` 命令行入口。

读取 UTF-8（可带 BOM）的温湿度样本，按 timestamp_ms 升序以演示时钟
立即回放：第一条样本的回放时间为零，其余样本按时间戳差值推进。结果以
JSON Lines 逐行写入标准输出；任何输入错误都只写入标准错误并以退出码 2 结束。

样本格式由 ``--format csv|jsonl`` 选择，默认 csv，不按扩展名推断。
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
from typing import Any

REQUIRED_COLUMNS = ("timestamp_ms", "temperature", "humidity")
FORMAT_CSV = "csv"
FORMAT_JSONL = "jsonl"

# timestamp_ms：仅非负十进制整数，不接受符号、空白、小数点或下划线。
# 注意用 \A...\Z 而非 ^...$：$ 会容忍末尾一个换行符，导致 "1000\n" 被误接受。
_TIMESTAMP_RE = re.compile(r"\A[0-9]+\Z")
# temperature/humidity：有限数值，允许小数和负数（不含 NaN/无穷/空白/下划线）。
_NUMBER_RE = re.compile(
    r"\A-?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?\Z"
)
_INTEGER_FORM_RE = re.compile(r"\A-?[0-9]+\Z")


def _json_key_pattern(name: str) -> str:
    """构造匹配某键名 JSON 字符串字面量的正则。

    键名的每个字符既可以是字面量，也可以写成 \\uXXXX 转义（含 A–F 大小写），
    因此 {"timestamp\\u005fms": -0} 这类转义键名也能被定位。
    """
    parts = ['"']
    for ch in name:
        hex_form = "".join(
            f"[{digit}{digit.swapcase()}]" if digit in "abcdef" else digit
            for digit in f"{ord(ch):04x}"
        )
        # 原始字符串中的 \\u 让正则匹配字面反斜杠 + u + 四位十六进制。
        parts.append(rf"(?:{re.escape(ch)}|\\u{hex_form})")
    parts.append('"')
    return "".join(parts)


# 定位 timestamp_ms 键值的负号：键（允许 \uXXXX 转义）+ 空白 + 冒号 + 空白 + '-'。
_JSONL_TIMESTAMP_MINUS_RE = re.compile(
    _json_key_pattern("timestamp_ms") + r"[ \t\n\r]*:[ \t\n\r]*-"
)

EXIT_OK = 0
EXIT_INPUT_ERROR = 2


class InputError(Exception):
    """输入内容不合法。

    CSV 模式下 record 为 CSV 记录序号（表头算第 1 条）；
    JSONL 模式下 line 为从 1 开始的物理行号（空白行也计数）。
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


class _DuplicateJsonObjectKey(Exception):
    """JSON 对象中出现重复键（object_pairs_hook 中抛出让 loads 中止）。"""

    def __init__(self, key: str):
        super().__init__(key)
        self.key = key


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJsonObjectKey(key)
        result[key] = value
    return result


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


def _emit_records(
    records: list[tuple[int, int | float, int | float]],
    start_ms: int | None,
    end_ms: int | None,
) -> str:
    """按闭区间筛选、稳定排序并生成 JSON Lines 输出。"""
    # 按时间区间筛选（闭区间，两端包含）；只选原始样本，不插值。
    if start_ms is not None:
        records = [r for r in records if r[0] >= start_ms]
    if end_ms is not None:
        records = [r for r in records if r[0] <= end_ms]

    # 没有样本落入区间：正常结束，标准输出为空。
    if not records:
        return ""

    # 稳定排序：时间戳升序；重复时间戳保留源文件中的先后顺序。
    records.sort(key=lambda item: item[0])
    first_timestamp = records[0][0]

    output = io.StringIO()
    for timestamp_ms, temperature, humidity in records:
        line = {
            "timestamp_ms": timestamp_ms,
            "elapsed_ms": timestamp_ms - first_timestamp,
            "temperature": temperature,
            "humidity": humidity,
        }
        output.write(json.dumps(line, separators=(",", ":")))
        output.write("\n")
    return output.getvalue()


def replay_csv(
    text: str,
    start_ms: int | None = None,
    end_ms: int | None = None,
) -> str:
    """解析 CSV 文本并返回 JSON Lines 输出字符串；非法时抛出 InputError。

    start_ms/end_ms 为可选的 timestamp_ms 闭区间端点；省略的一端不限制。
    先校验全部记录（区间外的非法数据同样使输入失败），再按区间筛选。
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

    return _emit_records(records, start_ms, end_ms)


def replay_text(
    text: str,
    start_ms: int | None = None,
    end_ms: int | None = None,
    file_format: str = FORMAT_CSV,
) -> str:
    """按指定格式解析文本并返回 JSON Lines 输出字符串；非法时抛出 InputError。

    file_format 为 ``csv`` 或 ``jsonl``，不按扩展名推断。
    """
    if file_format == FORMAT_JSONL:
        return replay_jsonl(text, start_ms, end_ms)
    return replay_csv(text, start_ms, end_ms)


def _jsonl_error(message: str, line: int) -> InputError:
    return InputError(message, line=line)


def replay_jsonl(
    text: str,
    start_ms: int | None = None,
    end_ms: int | None = None,
) -> str:
    """解析 JSONL 文本并返回 JSON Lines 输出字符串；非法时抛出 InputError。

    每个非空物理行必须恰好是一个 JSON 对象，且仅含 timestamp_ms、
    temperature、humidity 三个键（缺键、多键、重复键均拒绝）。
    先校验整个文件（区间外的非法记录同样使输入失败），再按区间筛选。
    空白行被忽略但仍计入物理行号；末行可以没有换行符。
    """
    records: list[tuple[int, int | float, int | float]] = []

    # splitlines 同时识别 \n、\r\n、\r 等物理行分隔；空白行也计入行号。
    for line_no, raw_line in enumerate(text.splitlines(), start=1):
        if not raw_line.strip():
            continue  # 忽略空白行，但其行号已经计入。

        try:
            item = json.loads(
                raw_line, object_pairs_hook=_reject_duplicate_keys
            )
        except _DuplicateJsonObjectKey as exc:
            raise _jsonl_error(
                f"对象包含重复键: {exc.key!r}（缺键、多键或重复键均拒绝）",
                line_no,
            )
        except json.JSONDecodeError as exc:
            # 结构性语法错误在此被拒；注意 json.loads 默认接受 NaN/Infinity
            # 等非标准记号，由后续的类型与有限性校验拦截。
            raise _jsonl_error(f"JSON 解析失败: {exc.msg}", line_no)
        if not isinstance(item, dict):
            raise _jsonl_error(
                f"每行必须是一个 JSON 对象，实际为 {_json_type_name(item)}",
                line_no,
            )

        keys = list(item.keys())
        if set(keys) != set(REQUIRED_COLUMNS):
            missing = [k for k in REQUIRED_COLUMNS if k not in keys]
            extra = [k for k in keys if k not in REQUIRED_COLUMNS]
            detail = []
            if missing:
                detail.append(f"缺少键: {', '.join(missing)}")
            if extra:
                detail.append(f"多余键: {', '.join(map(str, extra))}")
            raise _jsonl_error(
                "对象必须恰好包含 timestamp_ms、temperature、humidity 三键（"
                + "；".join(detail)
                + "）",
                line_no,
            )

        # 先校验温湿度类型：确认对象中除 timestamp_ms 键名外不存在其他
        # 字符串，随后对原始字面量做时间戳负号检测才不会误伤字符串内容。
        temperature = _jsonl_number_field(
            item["temperature"], "temperature", line_no
        )
        humidity = _jsonl_number_field(item["humidity"], "humidity", line_no)
        timestamp_ms = _jsonl_integer_field(item["timestamp_ms"], line_no)
        # 负号检测必须看原始字面量：-0 经 json.loads 后等于整数 0。
        # 正则识别键名（允许 \uXXXX 转义）后直接跟随的 '-'；同时以解析值
        # 的结构化判断兜底。
        if timestamp_ms < 0 or _JSONL_TIMESTAMP_MINUS_RE.search(raw_line):
            raise _jsonl_error(
                "timestamp_ms 非法（只接受不带负号的 JSON 整数字面量，"
                "拒绝负数、小数及指数）",
                line_no,
            )
        records.append((timestamp_ms, temperature, humidity))

    return _emit_records(records, start_ms, end_ms)


def _jsonl_integer_field(value: Any, line_no: int) -> int:
    """timestamp_ms：必须是 JSON 整数（bool 不算）；小数/指数解析为 float 被拒。"""
    # bool 是 int 的子类，必须在整数判断之前排除。
    if isinstance(value, bool) or not isinstance(value, int):
        raise _jsonl_error(
            "timestamp_ms 必须是不带负号的 JSON 整数字面量，实际为 "
            f"{_json_type_name(value)}: {json.dumps(value, ensure_ascii=False)}",
            line_no,
        )
    return value


def _jsonl_number_field(value: Any, column: str, line_no: int) -> int | float:
    """temperature/humidity：有限 JSON 数值，允许负数、小数、指数。

    字符串、布尔值、null、数组、对象一律拒绝；json.loads 默认会放行的
    NaN/Infinity 及溢出为无穷的指数写法（如 1e999）由此处的有限性检查拦截。
    """
    # bool 是 int 的子类，必须在数值判断之前排除。
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _jsonl_error(
            f"{column} 必须是有限 JSON 数值，实际为 "
            f"{_json_type_name(value)}: {json.dumps(value, ensure_ascii=False)}",
            line_no,
        )
    if isinstance(value, float) and not math.isfinite(value):
        raise _jsonl_error(
            f"{column} 必须为有限数值（拒绝浮点溢出）", line_no
        )
    return value


def _json_type_name(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "布尔值"
    if isinstance(value, str):
        return "字符串"
    if isinstance(value, (int, float)):
        return "数值"
    if isinstance(value, list):
        return "数组"
    if isinstance(value, dict):
        return "对象"
    return type(value).__name__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sensor_replay",
        description=(
            "读取 UTF-8（可带 BOM）温湿度样本，按 timestamp_ms 升序以演示"
            "时钟立即回放，逐行输出 JSON（timestamp_ms/elapsed_ms/"
            "temperature/humidity）。用 --format 选择 csv 或 jsonl。"
        ),
    )
    parser.add_argument(
        "file_path",
        help=(
            "温湿度样本文件路径（UTF-8 编码，可带 BOM；路径可含空格）；"
            "格式由 --format 决定，不按扩展名推断"
        ),
    )
    parser.add_argument(
        "--format",
        choices=(FORMAT_CSV, FORMAT_JSONL),
        default=FORMAT_CSV,
        help=(
            "样本文件格式：csv（默认）或 jsonl；不按文件扩展名推断。"
            "csv 为首行表头的三列 CSV；jsonl 为每行一个 JSON 对象，"
            "仅含 timestamp_ms、temperature、humidity 三键"
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
    return parser


def parse_bound(token: str, option: str) -> int:
    """校验 --start-ms/--end-ms 取值：仅 ASCII 数字组成的非负十进制整数。"""
    if not _TIMESTAMP_RE.match(token):
        raise InputError(
            f"{option} 参数非法（需非负十进制整数，不接受符号、小数、"
            f"指数或空白）: {token!r}"
        )
    return int(token)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    path = Path(args.file_path)
    file_format = args.format

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
    except InputError as exc:
        print(f"错误: {exc.message}", file=sys.stderr)
        return EXIT_INPUT_ERROR

    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        print(f"错误: 文件不存在: {args.file_path}", file=sys.stderr)
        return EXIT_INPUT_ERROR
    except IsADirectoryError:
        print(f"错误: 路径是目录而非文件: {args.file_path}", file=sys.stderr)
        return EXIT_INPUT_ERROR
    except OSError as exc:
        print(
            f"错误: 无法读取文件 {args.file_path}: {exc.strerror or exc}",
            file=sys.stderr,
        )
        return EXIT_INPUT_ERROR

    try:
        text = raw.decode("utf-8-sig")  # utf-8-sig 同时处理有无 BOM
    except UnicodeDecodeError:
        print(
            f"错误: 文件 {args.file_path} 不是有效的 UTF-8 编码",
            file=sys.stderr,
        )
        return EXIT_INPUT_ERROR

    try:
        output = replay_text(text, start_ms, end_ms, file_format)
    except InputError as exc:
        if exc.line is not None:
            location = f"第 {exc.line} 行: "
        else:
            location = (
                f"第 {exc.record} 条 CSV 记录: "
                if exc.record is not None
                else ""
            )
        print(f"错误: {location}{exc.message}", file=sys.stderr)
        return EXIT_INPUT_ERROR

    sys.stdout.write(output)
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
