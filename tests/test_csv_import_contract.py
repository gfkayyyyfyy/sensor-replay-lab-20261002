"""CSV 导入契约的命令行回归测试。

通过子进程调用 README 声明的默认入口 ``python -m sensor_replay``（**不**传
``--format``，默认即 CSV），用临时离线样本核对退出码、标准输出与标准错误，
证明 README 中的表头、数值与错误定位约定成立：

- 三列表头可任意换序，列与字段仍正确对应；
- UTF-8 BOM、CRLF 换行、末行无换行符均为合法文件；
- 成功时退出 0、标准错误为空，标准输出恰为两个四字段 JSON 对象，按
  timestamp_ms 升序回放；
- 表头缺列/多列/重复列/列名错误指向第 1 条 CSV 记录；
- 数据行少列/多列、时间戳非法、温湿度缺失或非有限值均退出 2、标准输出
  完全为空、标准错误无堆栈，并指向实际 CSV 记录序号（表头算第 1 条），
  数值错误点名对应字段；
- 引号包裹的跨行字段按 CSV 记录（而非物理行）定位，且不先输出合法记录。

在项目根目录执行::

    python -m unittest discover -s tests
    python -m unittest tests.test_csv_import_contract
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

# 换序三列表头：humidity、timestamp_ms、temperature。
REORDERED_HEADER = "humidity,timestamp_ms,temperature"
# 源文件顺序的两条数据：第一条 1000/-2.5/60（指数 6e1 与前导零 01000
# 写法），第二条 0/19.5/55。
REORDERED_ROWS = ["6e1,01000,-2.5", "55,0,19.5"]

# 同一批记录改成默认列序后的源文件顺序。
DEFAULT_HEADER = "timestamp_ms,temperature,humidity"
DEFAULT_ORDER_ROWS = ["01000,-2.5,6e1", "0,19.5,55"]

# 按 timestamp_ms 升序后的两条预期记录（6e1 解析为 60.0，与 60 等值）。
EXPECTED_RECORDS = [
    {"timestamp_ms": 0, "elapsed_ms": 0, "temperature": 19.5, "humidity": 55},
    {"timestamp_ms": 1000, "elapsed_ms": 1000, "temperature": -2.5,
     "humidity": 60},
]


def run_default(path: Path, *extra_args: str) -> subprocess.CompletedProcess:
    """不传 --format，走 README 声明的默认 CSV 入口。"""
    return subprocess.run(
        [sys.executable, "-m", "sensor_replay", str(path), *extra_args],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


class CsvContractTestCase(unittest.TestCase):
    """公共基类：在临时目录准备文件并提供契约级断言。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp_dir = Path(self._tmp.name)

    def write_text(
        self, text: str, name: str = "samples.csv", encoding: str = "utf-8"
    ) -> Path:
        path = self.tmp_dir / name
        path.write_text(text, encoding=encoding)
        return path

    def write_lines(self, lines: list[str], name: str = "samples.csv") -> Path:
        """以普通 UTF-8、LF 换行写入文本行（末行带换行）。"""
        return self.write_text("\n".join(lines) + "\n", name)

    def write_bytes(self, data: bytes, name: str = "samples.csv") -> Path:
        path = self.tmp_dir / name
        path.write_bytes(data)
        return path

    @staticmethod
    def parse_objects(stdout: str) -> list[dict]:
        """逐行解析 JSON Lines，断言每行恰为四个数值字段的 JSON 对象。"""
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

    def assert_two_expected_records(self, result: subprocess.CompletedProcess):
        """默认成功契约：退出 0、标准错误为空、恰有两个约定 JSON 对象。"""
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(result.stderr, "")
        # 标准输出恰有两个 JSON 对象（按非空行计）。
        lines = result.stdout.splitlines()
        self.assertEqual(len(lines), 2)
        records = self.parse_objects(result.stdout)
        # 比较字段和值（dict 等值不依赖 JSON 键顺序）。
        self.assertEqual(records, EXPECTED_RECORDS)
        # 再逐序列核对，避免字典等值掩盖表头换序导致的字段错配。
        self.assertEqual([r["timestamp_ms"] for r in records], [0, 1000])
        self.assertEqual([r["elapsed_ms"] for r in records], [0, 1000])
        self.assertEqual([r["temperature"] for r in records], [19.5, -2.5])
        self.assertEqual([r["humidity"] for r in records], [55, 60])
        return records

    def assert_csv_error(
        self,
        result: subprocess.CompletedProcess,
        record_no: int,
        field: str | None = None,
    ) -> None:
        """失败契约：退出 2、标准输出全空、标准错误无堆栈且定位到记录。"""
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertNotEqual(result.stderr, "")
        self.assertNotIn("Traceback", result.stderr)
        self.assertIn(f"第 {record_no} 条 CSV 记录", result.stderr)
        if field is not None:
            self.assertIn(field, result.stderr)


class TestCsvReorderedHeaderSuccess(CsvContractTestCase):
    """换序列头与 BOM/CRLF/无末行换行的合法文件。"""

    def test_default_entry_maps_reordered_columns(self) -> None:
        # 表头 humidity,timestamp_ms,temperature；默认入口（无 --format）。
        path = self.write_lines([REORDERED_HEADER, *REORDERED_ROWS])
        result = run_default(path)
        self.assert_two_expected_records(result)

    def test_bom_crlf_and_no_final_newline_accepted(self) -> None:
        # 同一批样本：UTF-8 BOM + CRLF 换行 + 末行无换行符。
        data = (
            b"\xef\xbb\xbf"
            + REORDERED_HEADER.encode("utf-8")
            + b"\r\n"
            + REORDERED_ROWS[0].encode("utf-8")
            + b"\r\n"
            + REORDERED_ROWS[1].encode("utf-8")  # 末行不带换行符
        )
        result = run_default(self.write_bytes(data, name="bom_crlf.csv"))
        self.assert_two_expected_records(result)

    def test_reordered_exotic_file_equals_default_order_plain_file(self) -> None:
        # 换序列头 + BOM/CRLF/无末行换行 vs 默认列序 + 普通 UTF-8/LF：
        # 解析后的字段与值完全相同（不比较 JSON 键顺序与原始文本）。
        exotic = (
            b"\xef\xbb\xbf"
            + REORDERED_HEADER.encode("utf-8")
            + b"\r\n"
            + REORDERED_ROWS[0].encode("utf-8")
            + b"\r\n"
            + REORDERED_ROWS[1].encode("utf-8")
        )
        plain_path = self.write_lines(
            [DEFAULT_HEADER, *DEFAULT_ORDER_ROWS], name="plain.csv"
        )
        exotic_records = self.parse_objects(
            run_default(self.write_bytes(exotic, name="exotic.csv")).stdout
        )
        plain_records = self.parse_objects(run_default(plain_path).stdout)
        self.assertEqual(exotic_records, plain_records)
        self.assertEqual(exotic_records, EXPECTED_RECORDS)


class TestCsvHeaderErrors(CsvContractTestCase):
    """表头缺列、多列、重复列、列名错误：一律指向第 1 条 CSV 记录。"""

    def assert_header_error(self, lines: list[str]) -> None:
        # 附带与错误表头同列数的数据行，证明被拒的是表头本身（记录 1），
        # 而不是后面的数据行。
        result = run_default(self.write_lines(lines))
        self.assert_csv_error(result, 1)

    def test_missing_column(self) -> None:
        self.assert_header_error(["timestamp_ms,temperature", "1,20"])

    def test_extra_column(self) -> None:
        self.assert_header_error(
            ["timestamp_ms,temperature,humidity,extra", "1,20,55,0"]
        )

    def test_duplicate_column(self) -> None:
        self.assert_header_error(
            ["timestamp_ms,temperature,temperature", "1,20,21"]
        )

    def test_wrong_column_name(self) -> None:
        self.assert_header_error(
            ["timestamp_ms,temperature,moisture", "1,20,55"]
        )


class TestCsvRowShapeErrors(CsvContractTestCase):
    """数据行少列/多列：指向实际 CSV 记录序号（首条数据为第 2 条）。"""

    def test_data_row_too_few(self) -> None:
        path = self.write_lines(
            [DEFAULT_HEADER, "0,19.5,55", "1000,-2.5"]  # 第 3 条仅 2 列
        )
        result = run_default(path)
        self.assert_csv_error(result, 3)
        self.assertIn("列数", result.stderr)

    def test_data_row_too_many(self) -> None:
        path = self.write_lines(
            [DEFAULT_HEADER, "0,19.5,55", "1000,-2.5,60,9"]  # 第 3 条 4 列
        )
        result = run_default(path)
        self.assert_csv_error(result, 3)
        self.assertIn("列数", result.stderr)


class TestCsvTimestampErrors(CsvContractTestCase):
    """timestamp_ms 负数、小数、带空白：退出 2 并点名字段。"""

    def test_timestamp_forms_rejected(self) -> None:
        # subTest 保留每个取值的独立定位；非法值均位于第二条数据行
        # （第 3 条 CSV 记录）。
        for bad in ("-1", "1.5", " 1", "1 "):
            with self.subTest(bad=bad):
                # 非法值位于第二条数据行（第 3 条 CSV 记录）。
                path = self.write_lines(
                    [DEFAULT_HEADER, "0,19.5,55", f"{bad},20,55"]
                )
                result = run_default(path)
                self.assert_csv_error(result, 3, "timestamp_ms")


class TestCsvNumberFieldErrors(CsvContractTestCase):
    """temperature/humidity 为空、非数值、NaN、Infinity、1e999。"""

    BAD_NUMBERS = ("", "abc", "NaN", "Infinity", "1e999")

    def test_temperature_forms_rejected(self) -> None:
        for bad in self.BAD_NUMBERS:
            with self.subTest(bad=bad):
                path = self.write_lines(
                    [DEFAULT_HEADER, "0,19.5,55", f"1000,{bad},60"]
                )
                result = run_default(path)
                self.assert_csv_error(result, 3, "temperature")

    def test_humidity_forms_rejected(self) -> None:
        for bad in self.BAD_NUMBERS:
            with self.subTest(bad=bad):
                path = self.write_lines(
                    [DEFAULT_HEADER, "0,19.5,55", f"1000,-2.5,{bad}"]
                )
                result = run_default(path)
                self.assert_csv_error(result, 3, "humidity")


class TestCsvQuotedMultilineRecord(CsvContractTestCase):
    """引号内真实换行的字段按 CSV 记录序号定位，而非物理行号。"""

    def test_multiline_quoted_field_reported_as_record_4(self) -> None:
        # 第 1 条：表头；第 2、3 条：合法数据；第 4 条 CSV 记录跨物理行
        # 第 4、5 行——temperature 字段被引号包住，内容为 "20\n5"。
        data = (
            b"timestamp_ms,temperature,humidity\n"
            b"0,19.5,55\n"
            b"1000,-2.5,60\n"
            b'7,"20\n5",58'
        )
        result = run_default(self.write_bytes(data, name="multiline.csv"))
        self.assert_csv_error(result, 4, "temperature")
        # 不能把物理行号（第 4 行起始、第 5 行结束）当成记录序号。
        self.assertNotIn("第 4 行", result.stderr)
        self.assertNotIn("第 5 行", result.stderr)
        # 两条合法记录不得被先行输出。
        self.assertEqual(result.stdout, "")


if __name__ == "__main__":
    unittest.main()
