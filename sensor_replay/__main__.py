"""CSV 温湿度样本回放入口：python -m sensor_replay samples.csv"""

import argparse
import csv
import json
import math
import re
import sys

EXPECTED_COLUMNS = ("timestamp_ms", "temperature", "humidity")
TIMESTAMP_PATTERN = re.compile(r"[0-9]+")


class InputError(Exception):
    """输入文件内容不合法。"""


def parse_timestamp(text, record_no):
    if not TIMESTAMP_PATTERN.fullmatch(text):
        raise InputError(
            f"第 {record_no} 条记录：timestamp_ms 必须是非负十进制整数，实际为 {text!r}"
        )
    return int(text)


def parse_measurement(text, column, record_no):
    if text == "":
        raise InputError(f"第 {record_no} 条记录：{column} 数值缺失")
    try:
        value = float(text)
    except ValueError:
        raise InputError(
            f"第 {record_no} 条记录：{column} 无法解析为数值，实际为 {text!r}"
        ) from None
    if not math.isfinite(value):
        raise InputError(
            f"第 {record_no} 条记录：{column} 必须是有限数值，实际为 {text!r}"
        )
    return value


def load_samples(path):
    """读取并校验 CSV，返回按 timestamp_ms 升序（稳定）排列的样本列表。"""
    with open(path, "r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.reader(handle))

    if not rows:
        raise InputError("文件为空，缺少表头")

    header = rows[0]
    if len(set(header)) != len(header):
        raise InputError("第 1 条记录（表头）：包含重复列")
    if len(header) != len(EXPECTED_COLUMNS) or set(header) != set(EXPECTED_COLUMNS):
        raise InputError(
            "第 1 条记录（表头）：必须恰好包含 "
            + "、".join(EXPECTED_COLUMNS)
            + " 三列，不接受额外列或缺失列"
        )
    column_index = {name: header.index(name) for name in EXPECTED_COLUMNS}

    samples = []
    for record_no, row in enumerate(rows[1:], start=2):
        if not row:
            continue  # 跳过完全空白的行
        if len(row) != len(EXPECTED_COLUMNS):
            raise InputError(
                f"第 {record_no} 条记录：列数不符，期望 {len(EXPECTED_COLUMNS)} 列，"
                f"实际 {len(row)} 列"
            )
        samples.append(
            (
                parse_timestamp(row[column_index["timestamp_ms"]], record_no),
                parse_measurement(
                    row[column_index["temperature"]], "temperature", record_no
                ),
                parse_measurement(row[column_index["humidity"]], "humidity", record_no),
            )
        )

    # 稳定排序：时间戳相同则保持源文件中的先后顺序
    samples.sort(key=lambda sample: sample[0])
    return samples


def json_number(value):
    """整数值以整数形式输出，其余保留浮点数值。"""
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def replay(samples, out):
    if not samples:
        return
    start_ms = samples[0][0]
    for timestamp_ms, temperature, humidity in samples:
        record = {
            "timestamp_ms": timestamp_ms,
            "elapsed_ms": timestamp_ms - start_ms,
            "temperature": json_number(temperature),
            "humidity": json_number(humidity),
        }
        out.write(json.dumps(record) + "\n")


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="sensor_replay",
        description="按 timestamp_ms 升序回放 CSV 温湿度样本，逐行输出 JSON。",
    )
    parser.add_argument("csv_path", help="CSV 样本文件路径（UTF-8，可带 BOM）")
    args = parser.parse_args(argv)

    try:
        samples = load_samples(args.csv_path)
    except InputError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2
    except FileNotFoundError:
        print(f"错误：文件不存在：{args.csv_path}", file=sys.stderr)
        return 2
    except UnicodeDecodeError as exc:
        print(f"错误：文件不是有效的 UTF-8 编码：{exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"错误：无法读取文件 {args.csv_path}：{exc}", file=sys.stderr)
        return 2

    replay(samples, sys.stdout)
    return 0


if __name__ == "__main__":
    sys.exit(main())
