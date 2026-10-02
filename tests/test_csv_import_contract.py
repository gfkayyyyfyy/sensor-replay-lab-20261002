"""CSV 导入契约回归测试。

针对 README 中 CSV 输入约定（表头三列任意顺序、数值写法、错误定位到
CSV 记录序号而非物理行号）在 ``python -m sensor_replay`` 默认 CSV 入口上
的端到端验证。测试自备临时离线样本，仅使用标准库，通过子进程核对退出码、
标准输出与标准错误；不修改产品源码，也不依赖仓库自带的样本文件。

在项目根目录执行::

    python -m unittest discover -s tests

或单独执行::

    python -m unittest tests/test_csv_import_contract.py
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

EXPECTED_KEYS = {"timestamp_ms", "elapsed_ms", "temperature", "humidity"}

# 换序表头与两条源顺序数据记录（README 允许列顺序任意）。
REORDERED_HEADER = "humidity,timestamp_ms,temperature"
REORDERED_ROWS = ["6e1,01000,-2.5", "55,0,19.5"]

# 同一批记录的默认列序写法（字面量与换序版一致）。
DEFAULT_HEADER = "timestamp_ms,temperature,humidity"
DEFAULT_ROWS = ["0,19.5,55", "01000,-2.5,6e1"]

# 两种写法解析后应得到完全相同的输出记录（按回放顺序）。
EXPECTED_RECORDS = [
    {"timestamp_ms": 0, "elapsed_ms": 0, "temperature": 19.5, "humidity": 55},
    {
        "timestamp_ms": 1000,
        "elapsed_ms": 1000,
        "temperature": -2.5,
        "humidity": 60,
    },
]


def run_replay(path: Path, *extra_args: str) -> subprocess.CompletedProcess:
    """以子进程运行默认 CSV 入口（不显式传 --format）并捕获结果。"""
    return subprocess.run(
        [sys.executable, "-m", "sensor_replay", str(path), *extra_args],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


class CsvImportContractTestCase(unittest.TestCase):
    """公共基类：临时目录、样本写入与结果断言助手。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp_dir = Path(self._tmp.name)

    def write_csv_bytes(self, data: bytes, name: str = "samples.csv") -> Path:
        path = self.tmp_dir / name
        path.write_bytes(data)
        return path

    def write_csv_text(
        self, text: str, name: str = "samples.csv"
    ) -> Path:
        return self.write_csv_bytes(text.encode("utf-8"), name)

    def write_csv_lines(
        self, lines: list[str], name: str = "samples.csv"
    ) -> Path:
        """以 LF 换行、末行带换行符的普通 UTF-8 写入给定行。"""
        return self.write_csv_text("\n".join(lines) + "\n", name)

    @staticmethod
    def parse_json_lines(stdout: str) -> list[dict]:
        """逐行解析 JSON Lines，并校验每行恰为既有四个数值字段。"""
        records = []
        for line in stdout.splitlines():
            record = json.loads(line)
            assert isinstance(record, dict)
            assert set(record.keys()) == EXPECTED_KEYS
            for value in record.values():
                assert isinstance(value, (int, float))
                assert not isinstance(value, bool)
            records.append(record)
        return records

    def assert_success(
        self, result: subprocess.CompletedProcess
    ) -> list[dict]:
        """断言退出码为 0、标准错误为空，返回解析后的输出记录。"""
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(result.stderr, "")
        return self.parse_json_lines(result.stdout)

    def assert_input_error(self, result: subprocess.CompletedProcess) -> None:
        """断言退出码为 2、标准输出完全为空、标准错误非空且无堆栈。"""
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertNotEqual(result.stderr, "")
        self.assertNotIn("Traceback", result.stderr)


class TestCsvImportSuccess(CsvImportContractTestCase):
    """成功路径：换序表头、BOM/CRLF/末行无换行，以及与默认列序等价。"""

    def test_reordered_header_maps_columns(self) -> None:
        path = self.write_csv_lines([REORDERED_HEADER, *REORDERED_ROWS])
        records = self.assert_success(run_replay(path))
        self.assertEqual(len(records), 2)
        # 字典比较不依赖 JSON 键顺序；字段集合已由解析助手校验。
        self.assertEqual(records, EXPECTED_RECORDS)
        self.assertEqual([r["timestamp_ms"] for r in records], [0, 1000])
        self.assertEqual([r["elapsed_ms"] for r in records], [0, 1000])
        self.assertEqual([r["temperature"] for r in records], [19.5, -2.5])
        self.assertEqual([r["humidity"] for r in records], [55, 60])

    def test_bom_crlf_and_no_final_newline(self) -> None:
        # 带 UTF-8 BOM、CRLF 换行且末行无换行符的合法文件。
        data = b"\xef\xbb\xbf" + "\r\n".join(
            [REORDERED_HEADER, *REORDERED_ROWS]
        ).encode("utf-8")
        path = self.write_csv_bytes(data, name="bom_crlf.csv")
        records = self.assert_success(run_replay(path))
        self.assertEqual(records, EXPECTED_RECORDS)

    def test_default_layout_plain_utf8_lf_same_output(self) -> None:
        # 默认列序 + 普通 UTF-8 + LF 换行：解析结果与换序表头完全一致。
        reordered = self.assert_success(
            run_replay(
                self.write_csv_lines(
                    [REORDERED_HEADER, *REORDERED_ROWS], name="reordered.csv"
                )
            )
        )
        default = self.assert_success(
            run_replay(
                self.write_csv_lines(
                    [DEFAULT_HEADER, *DEFAULT_ROWS], name="default.csv"
                )
            )
        )
        self.assertEqual(default, EXPECTED_RECORDS)
        self.assertEqual(default, reordered)


class TestCsvHeaderErrors(CsvImportContractTestCase):
    """表头缺列、多列、重复列、错误列名：一律指向第 1 条 CSV 记录。"""

    def assert_header_rejected(self, header: str) -> None:
        path = self.write_csv_lines([header, *DEFAULT_ROWS])
        result = run_replay(path)
        self.assert_input_error(result)
        self.assertIn("第 1 条 CSV 记录", result.stderr)

    def test_missing_column(self) -> None:
        self.assert_header_rejected("timestamp_ms,temperature")

    def test_extra_column(self) -> None:
        self.assert_header_rejected(
            "timestamp_ms,temperature,humidity,extra"
        )

    def test_duplicate_column(self) -> None:
        self.assert_header_rejected("timestamp_ms,temperature,temperature")

    def test_wrong_column_name(self) -> None:
        self.assert_header_rejected("timestamp_ms,temperature,moisture")


class TestCsvRowWidthErrors(CsvImportContractTestCase):
    """数据行列数与表头不符：指向实际记录序号。"""

    def test_row_with_too_few_columns(self) -> None:
        # 第一条数据行合法，第二条少一列：应报第 3 条 CSV 记录。
        path = self.write_csv_lines(
            [DEFAULT_HEADER, "0,19.5,55", "1000,-2.5"]
        )
        result = run_replay(path)
        self.assert_input_error(result)
        self.assertIn("第 3 条 CSV 记录", result.stderr)

    def test_row_with_too_many_columns(self) -> None:
        path = self.write_csv_lines([DEFAULT_HEADER, "0,19.5,55,99"])
        result = run_replay(path)
        self.assert_input_error(result)
        self.assertIn("第 2 条 CSV 记录", result.stderr)


class TestCsvTimestampErrors(CsvImportContractTestCase):
    """timestamp_ms 为负数、小数或带空白：拒绝并点名字段与记录序号。"""

    def assert_timestamp_rejected(self, token: str) -> None:
        path = self.write_csv_lines(
            [DEFAULT_HEADER, f"{token},19.5,55"]
        )
        result = run_replay(path)
        self.assert_input_error(result)
        self.assertIn("第 2 条 CSV 记录", result.stderr)
        self.assertIn("timestamp_ms", result.stderr)

    def test_negative(self) -> None:
        self.assert_timestamp_rejected("-1")

    def test_decimal(self) -> None:
        self.assert_timestamp_rejected("1.5")

    def test_whitespace(self) -> None:
        self.assert_timestamp_rejected(" 100")
        self.assert_timestamp_rejected("100 ")


class TestCsvValueErrors(CsvImportContractTestCase):
    """温湿度字段为空、非数值、NaN、Infinity 或 1e999：拒绝并点名字段。"""

    BAD_TOKENS = ["", "abc", "NaN", "Infinity", "1e999"]

    def assert_value_rejected(self, row: str, field: str) -> None:
        path = self.write_csv_lines([DEFAULT_HEADER, row])
        result = run_replay(path)
        self.assert_input_error(result)
        self.assertIn("第 2 条 CSV 记录", result.stderr)
        self.assertIn(field, result.stderr)

    def test_temperature_rejected(self) -> None:
        for token in self.BAD_TOKENS:
            with self.subTest(field="temperature", token=token):
                self.assert_value_rejected(f"0,{token},55", "temperature")

    def test_humidity_rejected(self) -> None:
        for token in self.BAD_TOKENS:
            with self.subTest(field="humidity", token=token):
                self.assert_value_rejected(f"0,19.5,{token}", "humidity")


class TestCsvQuotedNewlineRecord(CsvImportContractTestCase):
    """引号包裹的跨物理行字段：错误定位按 CSV 记录序号而非物理行号。"""

    def test_multiline_quoted_temperature_reports_record_4(self) -> None:
        # 表头与两条合法数据之后，追加一条时间戳和湿度合法、温度由引号
        # 包住且内含真实换行符的记录：物理上占两行，但只是第 4 条 CSV 记录。
        text = (
            DEFAULT_HEADER
            + "\n0,19.5,55"
            + "\n1000,-2.5,60"
            + '\n3000,"20\n5",60\n'
        )
        path = self.write_csv_text(text)
        result = run_replay(path)
        # 不能先输出前面的合法记录：标准输出必须完全为空。
        self.assert_input_error(result)
        self.assertIn("第 4 条 CSV 记录", result.stderr)
        self.assertIn("temperature", result.stderr)
        # 不得把物理行号（第 5 行）当成记录序号。
        self.assertNotIn("第 5 条 CSV 记录", result.stderr)


if __name__ == "__main__":
    unittest.main()
