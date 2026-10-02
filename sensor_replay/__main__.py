"""``python -m sensor_replay`` 命令行入口。

读取 UTF-8（可带 BOM）的温湿度 CSV 样本，按 timestamp_ms 升序以演示时钟
立即回放：第一条样本的回放时间为零，其余样本按时间戳差值推进。结果以
JSON Lines 逐行写入标准输出；任何输入错误都只写入标准错误并以退出码 2 结束。
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
_TIMESTAMP_RE = re.compile(r"^[0-9]+$")
# temperature/humidity：有限数值，允许小数和负数（不含 NaN/无穷/空白/下划线）。
_NUMBER_RE = re.compile(
    r"^-?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?$"
)
_INTEGER_FORM_RE = re.compile(r"^-?[0-9]+$")

EXIT_OK = 0
EXIT_INPUT_ERROR = 2


class InputError(Exception):
    """CSV 内容不合法。record 为 CSV 记录序号（表头算第 1 条）。"""

    def __init__(self, message: str, record: int | None = None):
        super().__init__(message)
        self.message = message
        self.record = record


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


def replay_text(
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

    # 按时间区间筛选（闭区间，两端包含）；只选原始样本，不插值。
    if start_ms is not None:
        records = [r for r in records if r[0] >= start_ms]
    if end_ms is not None:
        records = [r for r in records if r[0] <= end_ms]

    # 没有样本落入区间（或只有合法表头而没有数据）：正常结束，标准输出为空。
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sensor_replay",
        description=(
            "读取 UTF-8 CSV 温湿度样本，按 timestamp_ms 升序以演示时钟立即"
            "回放，逐行输出 JSON（timestamp_ms/elapsed_ms/temperature/humidity）。"
        ),
    )
    parser.add_argument(
        "csv_path",
        help="温湿度样本 CSV 文件路径（UTF-8 编码，可带 BOM；路径可含空格）",
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
    path = Path(args.csv_path)

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
        print(f"错误: 文件不存在: {args.csv_path}", file=sys.stderr)
        return EXIT_INPUT_ERROR
    except IsADirectoryError:
        print(f"错误: 路径是目录而非文件: {args.csv_path}", file=sys.stderr)
        return EXIT_INPUT_ERROR
    except OSError as exc:
        print(
            f"错误: 无法读取文件 {args.csv_path}: {exc.strerror or exc}",
            file=sys.stderr,
        )
        return EXIT_INPUT_ERROR

    try:
        text = raw.decode("utf-8-sig")  # utf-8-sig 同时处理有无 BOM
    except UnicodeDecodeError:
        print(
            f"错误: 文件 {args.csv_path} 不是有效的 UTF-8 编码",
            file=sys.stderr,
        )
        return EXIT_INPUT_ERROR

    try:
        output = replay_text(text, start_ms, end_ms)
    except InputError as exc:
        location = (
            f"第 {exc.record} 条 CSV 记录: " if exc.record is not None else ""
        )
        print(f"错误: {location}{exc.message}", file=sys.stderr)
        return EXIT_INPUT_ERROR

    sys.stdout.write(output)
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
