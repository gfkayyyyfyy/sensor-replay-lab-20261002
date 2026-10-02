"""CSV 读取级错误（csv.Error）归入输入错误的回归测试。

覆盖读取器在字段超过 csv.field_size_limit() 等情况下抛出 csv.Error 的
失败路径：命令须以退出码 2 结束、标准输出完全为空、标准错误给出包含
“CSV 解析失败”与记录位置的单条错误信息（无 Python 堆栈），且不继续回放
后续记录。记录位置按 CSV 记录序号定位（表头算第 1 条），引号包裹的跨
物理行字段仍只算一条记录。另含删去超长记录后的正常对照，以及字段校验
错误不被改报为 CSV 解析失败的反向用例。

在项目根目录执行::

    python -m unittest discover -s tests

或单独执行::

    python -m unittest tests/test_csv_parse_error.py
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
GOOD_ROWS = ["0,19.5,55", "1000,20.5,60"]

# 比 csv.field_size_limit() 返回值多一个字符的字段，触发读取级 csv.Error。
OVERSIZED_FIELD = "x" * (csv.field_size_limit() + 1)

EXPECTED_KEYS = {"timestamp_ms", "elapsed_ms", "temperature", "humidity"}


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
    """公共基类：临时目录、样本写入与结果断言助手。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp_dir = Path(self._tmp.name)

    def write_csv_text(self, text: str, name: str = "samples.csv") -> Path:
        path = self.tmp_dir / name
        path.write_bytes(text.encode("utf-8"))
        return path

    def write_csv_lines(
        self, lines: list[str], name: str = "samples.csv"
    ) -> Path:
        return self.write_csv_text("\n".join(lines) + "\n", name)

    def assert_csv_parse_error(
        self, result: subprocess.CompletedProcess, record: int
    ) -> None:
        """断言退出码 2、空标准输出、单条无堆栈的 CSV 解析失败定位信息。"""
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertNotEqual(result.stderr, "")
        self.assertNotIn("Traceback", result.stderr)
        self.assertIn("CSV 解析失败", result.stderr)
        self.assertIn(f"第 {record} 条 CSV 记录", result.stderr)


class TestCsvReadErrors(CsvParseErrorTestCase):
    """表头与数据记录读取触发 csv.Error 时的失败路径。"""

    def test_oversized_field_in_header_reports_record_1(self) -> None:
        # 超长字段位于文件首条记录（表头）：定位为第 1 条 CSV 记录。
        path = self.write_csv_lines(
            [f"timestamp_ms,{OVERSIZED_FIELD},humidity", *GOOD_ROWS]
        )
        self.assert_csv_parse_error(run_replay(path), 1)

    def test_oversized_field_in_data_reports_record_4(self) -> None:
        # 验收样本 bad.csv：表头 + 两条合法数据后，第三条数据（时间戳
        # 2000、湿度 61）的温度字段超长。即使 --end-ms 1000 会把该记录
        # 筛掉，读取仍须完成并使整个输入失败，定位为第 4 条 CSV 记录。
        path = self.write_csv_lines(
            [HEADER, *GOOD_ROWS, f"2000,{OVERSIZED_FIELD},61"],
            name="bad.csv",
        )
        result = run_replay(path, "--end-ms", "1000")
        self.assert_csv_parse_error(result, 4)
        # 前面两条合法记录不得提前输出。
        self.assertEqual(result.stdout, "")

    def test_explicit_format_csv_matches_default(self) -> None:
        # 默认 CSV 与显式 --format csv 的失败结果一致。
        lines = [HEADER, *GOOD_ROWS, f"2000,{OVERSIZED_FIELD},61"]
        default = run_replay(self.write_csv_lines(lines, name="default.csv"))
        explicit = subprocess.run(
            [
                sys.executable,
                "-m",
                "sensor_replay",
                str(self.write_csv_lines(lines, name="explicit.csv")),
                "--format",
                "csv",
            ],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        self.assert_csv_parse_error(default, 4)
        self.assert_csv_parse_error(explicit, 4)
        self.assertEqual(
            (default.returncode, default.stdout, default.stderr),
            (explicit.returncode, explicit.stdout, explicit.stderr),
        )

    def test_multiline_quoted_oversized_field_reports_record_4(self) -> None:
        # 超长字段由双引号包裹并跨越物理行：仍只按记录序号定位为第 4 条，
        # 不得把物理行号当成记录序号。
        text = (
            HEADER
            + "\n0,19.5,55"
            + "\n1000,20.5,60"
            + f'\n2000,"{OVERSIZED_FIELD}\nxx",61\n'
        )
        path = self.write_csv_text(text)
        result = run_replay(path)
        self.assert_csv_parse_error(result, 4)
        self.assertNotIn("第 5 条 CSV 记录", result.stderr)


class TestCsvParseErrorControl(CsvParseErrorTestCase):
    """正常对照与既有校验行为不受影响的反向用例。"""

    def test_replay_without_oversized_record_succeeds(self) -> None:
        # 删去超长记录后的普通回放：输出两条原有 JSON Lines，
        # elapsed_ms 依次为 0、1000，温湿度值不变，退出码 0 且标准错误为空。
        path = self.write_csv_lines([HEADER, *GOOD_ROWS])
        result = run_replay(path, "--end-ms", "1000")
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(result.stderr, "")
        records = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual(len(records), 2)
        for record in records:
            self.assertEqual(set(record.keys()), EXPECTED_KEYS)
        self.assertEqual(
            [r["elapsed_ms"] for r in records], [0, 1000]
        )
        self.assertEqual([r["temperature"] for r in records], [19.5, 20.5])
        self.assertEqual([r["humidity"] for r in records], [55, 60])

    def test_field_validation_error_is_not_csv_parse_error(self) -> None:
        # 既有字段校验错误的提示与定位保持不变，不得改报为 CSV 解析失败。
        path = self.write_csv_lines([HEADER, "0,abc,55"])
        result = run_replay(path)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertNotIn("Traceback", result.stderr)
        self.assertNotIn("CSV 解析失败", result.stderr)
        self.assertIn("第 2 条 CSV 记录", result.stderr)
        self.assertIn("temperature", result.stderr)


if __name__ == "__main__":
    unittest.main()
