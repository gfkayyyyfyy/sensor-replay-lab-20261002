"""``python -m sensor_replay`` 命令行回归测试。

测试自备临时 UTF-8 CSV 文件，通过子进程调用 ``python -m sensor_replay``，
核对退出码、标准输出和标准错误。不依赖仓库中的样本文件，也不等待真实
采样间隔（回放本身为演示时钟立即回放）。

在项目根目录执行::

    python -m unittest discover -s tests
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

HEADER = "timestamp_ms,temperature,humidity"

# 源文件顺序的五条数据记录（乱序且含重复时间戳）。
DATA_ROWS = [
    "2000,22,62",
    "1000,20.5,60",
    "0,19.5,55",
    "1000,21,61",
    "3000,23,63",
]

EXPECTED_KEYS = {"timestamp_ms", "elapsed_ms", "temperature", "humidity"}


def run_replay(csv_path: Path, *extra_args: str) -> subprocess.CompletedProcess:
    """以子进程运行 ``python -m sensor_replay`` 并捕获结果。"""
    return subprocess.run(
        [sys.executable, "-m", "sensor_replay", str(csv_path), *extra_args],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


class ReplayCliTestCase(unittest.TestCase):
    """公共基类：在临时目录中准备 CSV 文件。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp_dir = Path(self._tmp.name)

    def write_csv(self, lines: list[str], name: str = "samples.csv") -> Path:
        """把给定行写入临时 UTF-8 CSV 文件并返回路径。"""
        path = self.tmp_dir / name
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path

    def write_data_csv(
        self, extra_rows: list[str] | None = None, name: str = "samples.csv"
    ) -> Path:
        rows = list(DATA_ROWS)
        if extra_rows:
            rows.extend(extra_rows)
        return self.write_csv([HEADER, *rows], name)

    def write_jsonl(
        self, lines: list[str], name: str = "samples.jsonl"
    ) -> Path:
        """把给定物理行写入临时 UTF-8 JSONL 文件并返回路径。"""
        path = self.tmp_dir / name
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path

    def write_jsonl_bytes(self, data: bytes, name: str = "samples.jsonl") -> Path:
        """按原始字节写文件，用于 BOM、无末尾换行等场景。"""
        path = self.tmp_dir / name
        path.write_bytes(data)
        return path

    @staticmethod
    def parse_json_lines(stdout: str) -> list[dict]:
        """逐行解析 JSON Lines，并校验每行仅含四个数值字段。"""
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
        """断言退出码为 2、标准输出为空、标准错误非空且无堆栈。"""
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertNotEqual(result.stderr, "")
        self.assertNotIn("Traceback", result.stderr)


class TestIntervalReplay(ReplayCliTestCase):
    """闭区间回放的成功路径。"""

    def test_start_500_end_2000(self) -> None:
        path = self.write_data_csv()
        result = run_replay(path, "--start-ms", "500", "--end-ms", "2000")
        records = self.assert_success(result)
        self.assertEqual(
            [r["timestamp_ms"] for r in records], [1000, 1000, 2000]
        )
        self.assertEqual([r["elapsed_ms"] for r in records], [0, 0, 1000])
        self.assertEqual(
            [r["temperature"] for r in records], [20.5, 21, 22]
        )
        self.assertEqual([r["humidity"] for r in records], [60, 61, 62])

    def test_start_1000_end_2000_same_result(self) -> None:
        path = self.write_data_csv()
        result = run_replay(path, "--start-ms", "1000", "--end-ms", "2000")
        records = self.assert_success(result)
        self.assertEqual(
            [r["timestamp_ms"] for r in records], [1000, 1000, 2000]
        )
        self.assertEqual([r["elapsed_ms"] for r in records], [0, 0, 1000])
        self.assertEqual(
            [r["temperature"] for r in records], [20.5, 21, 22]
        )
        self.assertEqual([r["humidity"] for r in records], [60, 61, 62])

    def test_start_equals_end_keeps_duplicates(self) -> None:
        path = self.write_data_csv()
        result = run_replay(path, "--start-ms", "1000", "--end-ms", "1000")
        records = self.assert_success(result)
        self.assertEqual(
            [r["timestamp_ms"] for r in records], [1000, 1000]
        )
        self.assertEqual([r["elapsed_ms"] for r in records], [0, 0])
        # 重复时间戳保留源文件先后顺序。
        self.assertEqual(
            [r["temperature"] for r in records], [20.5, 21]
        )
        self.assertEqual([r["humidity"] for r in records], [60, 61])

    def test_only_start_2000(self) -> None:
        path = self.write_data_csv()
        result = run_replay(path, "--start-ms", "2000")
        records = self.assert_success(result)
        self.assertEqual(
            [r["timestamp_ms"] for r in records], [2000, 3000]
        )
        self.assertEqual([r["elapsed_ms"] for r in records], [0, 1000])
        self.assertEqual([r["temperature"] for r in records], [22, 23])
        self.assertEqual([r["humidity"] for r in records], [62, 63])

    def test_only_end_1000(self) -> None:
        path = self.write_data_csv()
        result = run_replay(path, "--end-ms", "1000")
        records = self.assert_success(result)
        self.assertEqual(
            [r["timestamp_ms"] for r in records], [0, 1000, 1000]
        )
        self.assertEqual([r["elapsed_ms"] for r in records], [0, 1000, 1000])
        self.assertEqual(
            [r["temperature"] for r in records], [19.5, 20.5, 21]
        )
        self.assertEqual([r["humidity"] for r in records], [55, 60, 61])

    def test_no_bounds_outputs_all_ascending(self) -> None:
        path = self.write_data_csv()
        result = run_replay(path)
        records = self.assert_success(result)
        self.assertEqual(
            [r["timestamp_ms"] for r in records],
            [0, 1000, 1000, 2000, 3000],
        )
        self.assertEqual(
            [r["elapsed_ms"] for r in records],
            [0, 1000, 1000, 2000, 3000],
        )
        self.assertEqual(
            [r["temperature"] for r in records],
            [19.5, 20.5, 21, 22, 23],
        )
        self.assertEqual(
            [r["humidity"] for r in records], [55, 60, 61, 62, 63]
        )

    def test_leading_zero_bounds_filter_as_integers(self) -> None:
        path = self.write_data_csv()
        result = run_replay(path, "--start-ms", "0500", "--end-ms", "02000")
        records = self.assert_success(result)
        self.assertEqual(
            [r["timestamp_ms"] for r in records], [1000, 1000, 2000]
        )
        self.assertEqual([r["elapsed_ms"] for r in records], [0, 0, 1000])


class TestEmptySuccess(ReplayCliTestCase):
    """无命中记录或只有表头时正常结束。"""

    def test_interval_without_hits(self) -> None:
        path = self.write_data_csv()
        result = run_replay(path, "--start-ms", "1500", "--end-ms", "1500")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "")

    def test_header_only_file(self) -> None:
        path = self.write_csv([HEADER])
        result = run_replay(path)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "")


class TestInvalidDataOutsideInterval(ReplayCliTestCase):
    """区间外的非法数据同样使整次输出失败。"""

    def test_nan_outside_interval_fails_whole_run(self) -> None:
        # 追加第 6 条数据行（即第 7 条 CSV 记录），落在所选区间之外。
        path = self.write_data_csv(extra_rows=["4000,NaN,70"])
        result = run_replay(path, "--start-ms", "500", "--end-ms", "2000")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertNotIn("Traceback", result.stderr)
        self.assertIn("第 7 条 CSV 记录", result.stderr)
        self.assertIn("temperature", result.stderr)


class TestInvalidBounds(ReplayCliTestCase):
    """--start-ms / --end-ms 参数非法时退出码为 2。"""

    def assert_bound_rejected(self, *extra_args: str) -> None:
        path = self.write_data_csv()
        result = run_replay(path, *extra_args)
        self.assert_input_error(result)

    def test_missing_value(self) -> None:
        self.assert_bound_rejected("--start-ms")
        self.assert_bound_rejected("--end-ms")

    def test_signed_value(self) -> None:
        self.assert_bound_rejected("--start-ms", "+100")
        self.assert_bound_rejected("--start-ms=-100")
        self.assert_bound_rejected("--end-ms", "+100")
        self.assert_bound_rejected("--end-ms=-100")

    def test_decimal_value(self) -> None:
        self.assert_bound_rejected("--start-ms", "1.5")
        self.assert_bound_rejected("--end-ms", "1000.0")

    def test_exponent_value(self) -> None:
        self.assert_bound_rejected("--start-ms", "1e3")
        self.assert_bound_rejected("--end-ms", "2E3")

    def test_whitespace_value(self) -> None:
        self.assert_bound_rejected("--start-ms", " 100")
        self.assert_bound_rejected("--end-ms", "100 ")

    def test_empty_string_value(self) -> None:
        self.assert_bound_rejected("--start-ms", "")
        self.assert_bound_rejected("--end-ms", "")

    def test_start_greater_than_end(self) -> None:
        self.assert_bound_rejected("--start-ms", "2000", "--end-ms", "1000")


# JSONL 模式使用的三行/五行样本，字段顺序与取值对应 DATA_ROWS。
JSONL_ACCEPTANCE_ROWS = [
    '{"timestamp_ms":2000,"temperature":22,"humidity":62}',
    '{"timestamp_ms":1000,"temperature":20.5,"humidity":60}',
    '{"timestamp_ms":1000,"temperature":21,"humidity":61}',
]

JSONL_DATA_ROWS = [
    '{"timestamp_ms":2000,"temperature":22,"humidity":62}',
    '{"timestamp_ms":1000,"temperature":20.5,"humidity":60}',
    '{"timestamp_ms":0,"temperature":19.5,"humidity":55}',
    '{"timestamp_ms":1000,"temperature":21,"humidity":61}',
    '{"timestamp_ms":3000,"temperature":23,"humidity":63}',
]


class TestJsonlIntervalReplay(ReplayCliTestCase):
    """JSONL 闭区间回放的成功路径（含验收用例）。"""

    def test_acceptance_start_500_end_2000(self) -> None:
        path = self.write_jsonl(JSONL_ACCEPTANCE_ROWS, name="demo.jsonl")
        result = run_replay(
            path, "--format", "jsonl", "--start-ms", "500", "--end-ms", "2000"
        )
        records = self.assert_success(result)
        self.assertEqual(
            [r["timestamp_ms"] for r in records], [1000, 1000, 2000]
        )
        self.assertEqual([r["elapsed_ms"] for r in records], [0, 0, 1000])
        self.assertEqual(
            [r["temperature"] for r in records], [20.5, 21, 22]
        )
        self.assertEqual([r["humidity"] for r in records], [60, 61, 62])

    def test_no_bounds_outputs_all_ascending(self) -> None:
        path = self.write_jsonl(JSONL_DATA_ROWS)
        result = run_replay(path, "--format", "jsonl")
        records = self.assert_success(result)
        self.assertEqual(
            [r["timestamp_ms"] for r in records],
            [0, 1000, 1000, 2000, 3000],
        )
        self.assertEqual(
            [r["elapsed_ms"] for r in records],
            [0, 1000, 1000, 2000, 3000],
        )

    def test_last_line_without_newline(self) -> None:
        # 末行可无换行符：按字节写入，不加结尾 \n。
        data = "\n".join(JSONL_ACCEPTANCE_ROWS).encode("utf-8")
        path = self.write_jsonl_bytes(data)
        result = run_replay(path, "--format", "jsonl")
        records = self.assert_success(result)
        self.assertEqual(len(records), 3)

    def test_bom_accepted(self) -> None:
        data = ("\n".join(JSONL_ACCEPTANCE_ROWS) + "\n").encode("utf-8-sig")
        path = self.write_jsonl_bytes(data)
        result = run_replay(path, "--format", "jsonl")
        self.assert_success(result)

    def test_blank_lines_ignored_but_counted(self) -> None:
        # 对象之间夹杂空白行（含空格、制表符）不影响输出。
        lines = [
            JSONL_DATA_ROWS[0],
            "",
            "   ",
            "\t",
            JSONL_DATA_ROWS[1],
            "",
        ]
        path = self.write_jsonl(lines)
        result = run_replay(path, "--format", "jsonl")
        records = self.assert_success(result)
        self.assertEqual(
            [r["timestamp_ms"] for r in records], [1000, 2000]
        )

    def test_negative_decimal_and_exponent_numbers_allowed(self) -> None:
        path = self.write_jsonl(
            ['{"timestamp_ms":0,"temperature":-3.5,"humidity":-2e1}']
        )
        result = run_replay(path, "--format", "jsonl")
        records = self.assert_success(result)
        self.assertEqual(records[0]["temperature"], -3.5)
        self.assertEqual(records[0]["humidity"], -20.0)

    def test_interval_without_hits_is_empty_success(self) -> None:
        path = self.write_jsonl(JSONL_DATA_ROWS)
        result = run_replay(
            path, "--format", "jsonl", "--start-ms", "1500", "--end-ms", "1500"
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "")


class TestJsonlEmptySuccess(ReplayCliTestCase):
    """空 JSONL 或仅含空白行时正常结束，两流为空。"""

    def test_empty_file(self) -> None:
        path = self.write_jsonl_bytes(b"")
        result = run_replay(path, "--format", "jsonl")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "")

    def test_only_blank_lines(self) -> None:
        path = self.write_jsonl(["", "  ", "\t", ""])
        result = run_replay(path, "--format", "jsonl")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "")


class TestJsonlValidationErrors(ReplayCliTestCase):
    """JSONL 语法、字段、数值错误：退出码 2、空标准输出、注明物理行号。"""

    def assert_jsonl_line_error(
        self, lines: list[str], line_no: int, *extra_args: str
    ) -> None:
        path = self.write_jsonl(lines)
        result = run_replay(path, "--format", "jsonl", *extra_args)
        self.assert_input_error(result)
        self.assertIn(f"第 {line_no} 行", result.stderr)

    def test_null_temperature_on_line_4_acceptance(self) -> None:
        # 验收：追加第 4 行 null 字段，即使落在区间外也使整次回放失败。
        lines = [
            *JSONL_ACCEPTANCE_ROWS,
            '{"timestamp_ms":3000,"temperature":null,"humidity":70}',
        ]
        path = self.write_jsonl(lines, name="demo.jsonl")
        result = run_replay(
            path, "--format", "jsonl", "--start-ms", "500", "--end-ms", "2000"
        )
        self.assert_input_error(result)
        self.assertIn("第 4 行", result.stderr)
        self.assertIn("temperature", result.stderr)

    def test_missing_key(self) -> None:
        self.assert_jsonl_line_error(
            ['{"timestamp_ms":1,"temperature":2}'], 1
        )

    def test_extra_key(self) -> None:
        self.assert_jsonl_line_error(
            ['{"timestamp_ms":1,"temperature":2,"humidity":3,"x":4}'], 1
        )

    def test_duplicate_key(self) -> None:
        self.assert_jsonl_line_error(
            [
                '{"timestamp_ms":1,"temperature":2,'
                '"temperature":3,"humidity":4}'
            ],
            1,
        )

    def test_timestamp_negative(self) -> None:
        self.assert_jsonl_line_error(
            ['{"timestamp_ms":-5,"temperature":1,"humidity":2}'], 1
        )

    def test_timestamp_negative_zero(self) -> None:
        # -0 解析后等于 0，必须按原始字面量的负号拒绝。
        self.assert_jsonl_line_error(
            ['{"timestamp_ms":-0,"temperature":1,"humidity":2}'], 1
        )

    def test_timestamp_negative_with_escaped_key(self) -> None:
        # 键名用 \uXXXX 转义（timestamp_ms 中的下划线写作 ）。
        self.assert_jsonl_line_error(
            ['{"timestamp\\u005fms":-0,"temperature":1,"humidity":2}'], 1
        )

    def test_timestamp_positive_with_escaped_key_accepted(self) -> None:
        path = self.write_jsonl(
            ['{"timestamp\\u005fms":5,"temperature":1,"humidity":2}']
        )
        result = run_replay(path, "--format", "jsonl")
        records = self.assert_success(result)
        self.assertEqual(records[0]["timestamp_ms"], 5)

    def test_timestamp_decimal(self) -> None:
        self.assert_jsonl_line_error(
            ['{"timestamp_ms":1.5,"temperature":1,"humidity":2}'], 1
        )

    def test_timestamp_exponent(self) -> None:
        self.assert_jsonl_line_error(
            ['{"timestamp_ms":1e3,"temperature":1,"humidity":2}'], 1
        )

    def test_timestamp_string_bool_null_rejected(self) -> None:
        for bad in ('"1000"', "true", "null"):
            self.assert_jsonl_line_error(
                [
                    '{"timestamp_ms":' + bad
                    + ',"temperature":1,"humidity":2}'
                ],
                1,
            )

    def test_number_fields_string_bool_null_rejected(self) -> None:
        for column, bad_value in (
            ("temperature", '"x"'),
            ("temperature", "true"),
            ("humidity", "false"),
            ("humidity", "null"),
        ):
            if column == "temperature":
                line = (
                    '{"timestamp_ms":1,"temperature":'
                    + bad_value
                    + ',"humidity":2}'
                )
            else:
                line = (
                    '{"timestamp_ms":1,"temperature":1,"humidity":'
                    + bad_value
                    + "}"
                )
            self.assert_jsonl_line_error([line], 1)

    def test_nan_infinity_and_overflow_rejected(self) -> None:
        for bad in ("NaN", "Infinity", "-Infinity", "1e999"):
            self.assert_jsonl_line_error(
                [
                    '{"timestamp_ms":1,"temperature":'
                    + bad
                    + ',"humidity":2}'
                ],
                1,
            )

    def test_syntax_error(self) -> None:
        self.assert_jsonl_line_error(["{not json"], 1)

    def test_non_object_lines(self) -> None:
        self.assert_jsonl_line_error(["[1,2,3]"], 1)
        self.assert_jsonl_line_error(['"str"'], 1)
        self.assert_jsonl_line_error(["42"], 1)
        self.assert_jsonl_line_error(["null"], 1)

    def test_two_objects_on_one_line_rejected(self) -> None:
        self.assert_jsonl_line_error(
            [
                '{"timestamp_ms":1,"temperature":1,"humidity":2}'
                '{"timestamp_ms":2,"temperature":1,"humidity":2}'
            ],
            1,
        )

    def test_blank_lines_count_in_physical_line_number(self) -> None:
        # 第 1 行为合法对象，第 2、3 行为空白，第 4 行为非法对象。
        self.assert_jsonl_line_error(
            [
                JSONL_DATA_ROWS[0],
                "",
                "  ",
                '{"timestamp_ms":1,"temperature":1}',
            ],
            4,
        )

    def test_invalid_record_outside_interval_fails_whole_run(self) -> None:
        # 非法对象的时间戳在区间之外，仍然使整次回放失败。
        self.assert_jsonl_line_error(
            [
                *JSONL_DATA_ROWS,
                '{"timestamp_ms":9000,"temperature":null,"humidity":70}',
            ],
            6,
            "--start-ms",
            "500",
            "--end-ms",
            "2000",
        )

    def test_whole_file_validated_before_filtering(self) -> None:
        # 首条记录合法且在区间内，后续非法记录仍令本次回放失败。
        self.assert_jsonl_line_error(
            [
                '{"timestamp_ms":1000,"temperature":20,"humidity":60}',
                '{"timestamp_ms":5000,"temperature":NaN,"humidity":60}',
            ],
            2,
            "--start-ms",
            "500",
            "--end-ms",
            "2000",
        )


class TestFormatSelection(ReplayCliTestCase):
    """--format 参数本身的行为。"""

    def test_default_is_csv_without_extension_inference(self) -> None:
        # 文件内容是 JSONL，但默认按 csv 解析，应报 CSV 表头错误。
        path = self.write_jsonl(JSONL_ACCEPTANCE_ROWS, name="demo.jsonl")
        result = run_replay(path)
        self.assert_input_error(result)
        self.assertIn("CSV", result.stderr)

    def test_extension_not_inferred_for_csv(self) -> None:
        # 扩展名是 .csv，显式要求 jsonl，应按 JSONL 解析失败。
        path = self.write_csv([HEADER, *DATA_ROWS], name="samples.csv")
        result = run_replay(path, "--format", "jsonl")
        self.assert_input_error(result)
        self.assertIn("第 1 行", result.stderr)

    def test_invalid_choice(self) -> None:
        path = self.write_jsonl(JSONL_ACCEPTANCE_ROWS)
        result = run_replay(path, "--format", "xml")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertNotIn("Traceback", result.stderr)

    def test_missing_format_value(self) -> None:
        path = self.write_jsonl(JSONL_ACCEPTANCE_ROWS)
        result = run_replay(path, "--format")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertNotIn("Traceback", result.stderr)

    def test_equals_form(self) -> None:
        path = self.write_jsonl(JSONL_ACCEPTANCE_ROWS)
        result = run_replay(path, "--format=jsonl")
        self.assert_success(result)


class TestJsonlFileErrors(ReplayCliTestCase):
    """JSONL 模式下文件与编码错误沿用退出码 2 约定。"""

    def test_missing_file(self) -> None:
        result = run_replay(
            self.tmp_dir / "nope.jsonl", "--format", "jsonl"
        )
        self.assert_input_error(result)

    def test_directory(self) -> None:
        result = run_replay(self.tmp_dir, "--format", "jsonl")
        self.assert_input_error(result)

    def test_invalid_utf8(self) -> None:
        path = self.write_jsonl_bytes(b"\xff\xfe{}\n")
        result = run_replay(path, "--format", "jsonl")
        self.assert_input_error(result)


if __name__ == "__main__":
    unittest.main()
