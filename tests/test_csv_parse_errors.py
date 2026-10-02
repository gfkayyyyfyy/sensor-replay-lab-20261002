"""CSV 解析失败（csv.Error）回归测试。

针对超过 csv.field_size_limit() 字段长度上限等会令底层 csv 模块抛出
csv.Error 的输入：入口必须把它归入既有输入错误约定——退出码 2、标准
输出完全为空、标准错误给出含“CSV 解析失败”和记录位置的一条信息、
无 Python 堆栈，且不继续回放后续记录。

定位按**逻辑 CSV 记录序号**（表头算第 1 条），与物理行号无关：被双
引号包裹且跨越物理行的字段仍只按记录序号定位。区间外或重复点策略本
会舍弃的记录同样要完成读取，超长字段仍使整个输入失败。

在项目根目录执行::

    python -m unittest discover -s tests

或单独执行::

    python -m unittest tests/test_csv_parse_errors.py
"""

from __future__ import annotations

import csv
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

HEADER = "timestamp_ms,temperature,humidity"

# 验收基线：表头后两条合法数据，第三条数据温度字段超长。
GOOD_ROWS = ["0,19.5,55", "1000,20.5,60"]
BAD_ROW_TEMPLATE = "2000,{temperature},61"

EXPECTED_RECORDS = [
    {"timestamp_ms": 0, "elapsed_ms": 0, "temperature": 19.5, "humidity": 55},
    {"timestamp_ms": 1000, "elapsed_ms": 1000, "temperature": 20.5, "humidity": 60},
]

# 比 csv 模块当前字段长度上限多一个字符的连续字母 x。
FIELD_LIMIT = csv.field_size_limit()
TOO_LONG_FIELD = "x" * (FIELD_LIMIT + 1)


def run_replay(path: Path, *extra_args: str) -> subprocess.CompletedProcess:
    """以子进程运行默认 CSV 入口（不显式传 --format）并捕获结果。"""
    return subprocess.run(
        [sys.executable, "-m", "sensor_replay", str(path), *extra_args],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


class CsvParseErrorTestCase(unittest.TestCase):
    """公共基类：临时目录、写入助手与错误约定断言。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp_dir = Path(self._tmp.name)

    def write_text(self, text: str, name: str = "bad.csv") -> Path:
        path = self.tmp_dir / name
        path.write_text(text, encoding="utf-8")
        return path

    def write_lines(self, lines: list[str], name: str = "bad.csv") -> Path:
        """以 LF 换行、末行带换行符的普通 UTF-8 写入给定行。"""
        return self.write_text("\n".join(lines) + "\n", name)

    def assert_csv_parse_error(
        self, result: subprocess.CompletedProcess, record_no: int
    ) -> None:
        """断言 csv.Error 失败路径完整遵循输入错误约定。"""
        self.assertEqual(result.returncode, 2)
        # 此前合法记录不能提前输出：标准输出必须完全为空。
        self.assertEqual(result.stdout, "")
        self.assertIn("CSV 解析失败", result.stderr)
        self.assertIn(f"第 {record_no} 条 CSV 记录", result.stderr)
        self.assertNotIn("Traceback", result.stderr)


class TestCsvParseFailurePosition(CsvParseErrorTestCase):
    """超长字段触发的 csv.Error：表头与数据记录分别定位。"""

    def test_data_record_failure_reports_record_4(self) -> None:
        # 离线文件 bad.csv：表头 + 两条合法数据 + 第三条温度超长。
        # 已成功读出两条数据后，下一条读取失败定位为第 4 条 CSV 记录。
        path = self.write_lines(
            [HEADER, *GOOD_ROWS, BAD_ROW_TEMPLATE.format(temperature=TOO_LONG_FIELD)]
        )
        # 区间只到 1000：超长记录落在区间外，但仍须完成读取并使输入失败。
        result = run_replay(path, "--end-ms", "1000")
        self.assert_csv_parse_error(result, 4)

    def test_explicit_format_csv_same_result(self) -> None:
        # 默认 CSV 与显式 --format csv 的失败结果一致。
        path = self.write_lines(
            [HEADER, *GOOD_ROWS, BAD_ROW_TEMPLATE.format(temperature=TOO_LONG_FIELD)]
        )
        result = run_replay(path, "--format", "csv", "--end-ms", "1000")
        self.assert_csv_parse_error(result, 4)

    def test_failure_in_first_record_reports_record_1(self) -> None:
        # 将超长字段放在文件首条记录（表头列名超长）：定位为第 1 条。
        path = self.write_lines(
            [f"timestamp_ms,temperature,{TOO_LONG_FIELD}", *GOOD_ROWS]
        )
        result = run_replay(path)
        self.assert_csv_parse_error(result, 1)

    def test_overlong_field_in_first_data_row_reports_record_2(self) -> None:
        # 首条数据（第 2 条 CSV 记录）即超长：表头已成功，定位为第 2 条。
        path = self.write_lines(
            [HEADER, BAD_ROW_TEMPLATE.format(temperature=TOO_LONG_FIELD)]
        )
        result = run_replay(path)
        self.assert_csv_parse_error(result, 2)

    def test_quoted_multiline_field_still_uses_record_number(self) -> None:
        # 超长温度字段由双引号包裹并跨越物理行：物理上占两行，错误位置
        # 仍按逻辑记录序号定位为第 4 条，不得报成物理行号（第 5 条）。
        head_length = FIELD_LIMIT // 2
        tail_length = FIELD_LIMIT + 1 - head_length
        multiline_field = '"' + ("x" * head_length) + "\n" + ("x" * tail_length) + '"'
        path = self.write_lines(
            [
                HEADER,
                *GOOD_ROWS,
                BAD_ROW_TEMPLATE.format(temperature=multiline_field),
            ]
        )
        result = run_replay(path)
        self.assert_csv_parse_error(result, 4)
        self.assertNotIn("第 5 条 CSV 记录", result.stderr)


class TestCsvParseFailureStillReadEntireFile(CsvParseErrorTestCase):
    """即使记录会被区间或重复策略舍弃，读取阶段仍必须到达并失败。"""

    def test_overlong_record_outside_start_bound_still_fails(self) -> None:
        # 超长记录时间戳 2000，位于 --start-ms 5000 的区间之外。
        path = self.write_lines(
            [HEADER, *GOOD_ROWS, BAD_ROW_TEMPLATE.format(temperature=TOO_LONG_FIELD)]
        )
        result = run_replay(path, "--start-ms", "5000")
        self.assert_csv_parse_error(result, 4)

    def test_overlong_duplicate_discarded_by_first_policy_still_fails(self) -> None:
        # 超长记录与上一条同戳，--duplicate-policy first 本会舍弃它，
        # 但读取先于策略：超长字段仍使整个输入失败。
        path = self.write_lines(
            [
                HEADER,
                "0,19.5,55",
                "1000,20.5,60",
                f"1000,{TOO_LONG_FIELD},61",
            ]
        )
        result = run_replay(path, "--duplicate-policy", "first")
        self.assert_csv_parse_error(result, 4)

    def test_no_playback_after_failure(self) -> None:
        # 超长记录之后再追加合法记录：失败后不得继续回放，也不得输出
        # 此前任何合法记录（整文件先校验后输出）。
        path = self.write_lines(
            [
                HEADER,
                *GOOD_ROWS,
                BAD_ROW_TEMPLATE.format(temperature=TOO_LONG_FIELD),
                "3000,23.5,63",
            ]
        )
        result = run_replay(path)
        self.assert_csv_parse_error(result, 4)


class TestNormalControlAfterRemovingOverlongRecord(CsvParseErrorTestCase):
    """删去超长记录后的普通回放：输出、退出码与标准错误均恢复正常。"""

    def test_plain_replay_outputs_two_records(self) -> None:
        path = self.write_lines([HEADER, *GOOD_ROWS], name="ok.csv")
        result = run_replay(path)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        records = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual(records, EXPECTED_RECORDS)
        self.assertEqual([r["elapsed_ms"] for r in records], [0, 1000])
        self.assertEqual([r["temperature"] for r in records], [19.5, 20.5])
        self.assertEqual([r["humidity"] for r in records], [55, 60])

    def test_replay_with_end_bound_outputs_two_records(self) -> None:
        # 与 bad.csv 验收相同的 --end-ms 1000 参数，仅删去超长记录。
        path = self.write_lines([HEADER, *GOOD_ROWS], name="ok.csv")
        result = run_replay(path, "--end-ms", "1000")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        records = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual(records, EXPECTED_RECORDS)

    def test_field_at_exact_limit_is_accepted(self) -> None:
        # 恰好等于上限的字段不触发 csv.Error，按既有温湿度校验失败处理
        # （非数字），错误归类保持原有字段校验提示，不改报 CSV 解析失败。
        exact_limit = "x" * FIELD_LIMIT
        path = self.write_lines(
            [HEADER, *GOOD_ROWS, BAD_ROW_TEMPLATE.format(temperature=exact_limit)]
        )
        result = run_replay(path)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn("第 4 条 CSV 记录", result.stderr)
        self.assertIn("temperature", result.stderr)
        self.assertNotIn("CSV 解析失败", result.stderr)
        self.assertNotIn("Traceback", result.stderr)


class TestExistingValidationMessagesUnchanged(CsvParseErrorTestCase):
    """已有表头、列数、时间戳与数值校验错误不得改报为 CSV 解析失败。"""

    def test_field_validation_error_not_reclassified(self) -> None:
        path = self.write_lines([HEADER, *GOOD_ROWS, "2000,NaN,61"])
        result = run_replay(path)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn("第 4 条 CSV 记录", result.stderr)
        self.assertIn("temperature", result.stderr)
        self.assertNotIn("CSV 解析失败", result.stderr)

    def test_timestamp_validation_error_not_reclassified(self) -> None:
        path = self.write_lines([HEADER, "0,19.5,55", "oops,20.5,60"])
        result = run_replay(path)
        self.assertEqual(result.returncode, 2)
        self.assertIn("第 3 条 CSV 记录", result.stderr)
        self.assertIn("timestamp_ms", result.stderr)
        self.assertNotIn("CSV 解析失败", result.stderr)

    def test_row_width_error_not_reclassified(self) -> None:
        path = self.write_lines([HEADER, *GOOD_ROWS, "2000,21.5"])
        result = run_replay(path)
        self.assertEqual(result.returncode, 2)
        self.assertIn("第 4 条 CSV 记录", result.stderr)
        self.assertIn("列数不符", result.stderr)
        self.assertNotIn("CSV 解析失败", result.stderr)

    def test_header_error_not_reclassified(self) -> None:
        path = self.write_lines(["timestamp_ms,temperature,extra", *GOOD_ROWS])
        result = run_replay(path)
        self.assertEqual(result.returncode, 2)
        self.assertIn("第 1 条 CSV 记录", result.stderr)
        self.assertIn("表头不合法", result.stderr)
        self.assertNotIn("CSV 解析失败", result.stderr)

    def test_jsonl_entry_keeps_original_behavior(self) -> None:
        # JSONL 入口不因本次改动而变化：超长字符串按 JSONL 字段校验，
        # 报物理行号而非 CSV 记录位置，也不出现“CSV 解析失败”。
        long_json_string = json.dumps(TOO_LONG_FIELD)
        path = self.write_text(
            f'{{"timestamp_ms":0,"temperature":{long_json_string},"humidity":55}}\n',
            name="big.jsonl",
        )
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "sensor_replay",
                str(path),
                "--format",
                "jsonl",
            ],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn("第 1 行", result.stderr)
        self.assertNotIn("CSV 解析失败", result.stderr)
        self.assertNotIn("Traceback", result.stderr)


if __name__ == "__main__":
    unittest.main()
