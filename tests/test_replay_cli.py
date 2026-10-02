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

# JSONL 版的源顺序三/五条样本（与 CSV 数据语义一致，乱序且含重复时间戳）。
JSONL_ROWS = [
    {"timestamp_ms": 2000, "temperature": 22, "humidity": 62},
    {"timestamp_ms": 1000, "temperature": 20.5, "humidity": 60},
    {"timestamp_ms": 0, "temperature": 19.5, "humidity": 55},
    {"timestamp_ms": 1000, "temperature": 21, "humidity": 61},
    {"timestamp_ms": 3000, "temperature": 23, "humidity": 63},
]

EXPECTED_KEYS = {"timestamp_ms", "elapsed_ms", "temperature", "humidity"}


def run_replay(
    path: Path, *extra_args: str, data_format: str = "csv"
) -> subprocess.CompletedProcess:
    """以子进程运行 ``python -m sensor_replay`` 并捕获结果。"""
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "sensor_replay",
            str(path),
            "--format",
            data_format,
            *extra_args,
        ],
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
        self, lines: list[str], name: str = "samples.jsonl", bom: bool = False
    ) -> Path:
        """把给定原始行写入临时 JSONL 文件（默认末尾带换行）。"""
        path = self.tmp_dir / name
        data = ("\n".join(lines) + "\n").encode("utf-8")
        if bom:
            data = b"\xef\xbb\xbf" + data
        path.write_bytes(data)
        return path

    def write_jsonl_objects(
        self,
        records: list[dict] | None = None,
        *,
        name: str = "samples.jsonl",
        trailing_newline: bool = True,
    ) -> Path:
        """把 JSON 对象列表序列化为 JSONL 临时文件并返回路径。"""
        records = JSONL_ROWS if records is None else records
        path = self.tmp_dir / name
        text = "\n".join(json.dumps(r, separators=(",", ":")) for r in records)
        if trailing_newline:
            text += "\n"
        path.write_text(text, encoding="utf-8")
        return path

    def run_jsonl(
        self, path: Path, *extra_args: str
    ) -> subprocess.CompletedProcess:
        return run_replay(path, *extra_args, data_format="jsonl")

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


class TestJsonlIntervalReplay(ReplayCliTestCase):
    """JSONL 闭区间回放的成功路径（验收用例）。"""

    def test_demo_acceptance(self) -> None:
        path = self.write_jsonl_objects(
            [
                {"timestamp_ms": 2000, "temperature": 22, "humidity": 62},
                {"timestamp_ms": 1000, "temperature": 20.5, "humidity": 60},
                {"timestamp_ms": 1000, "temperature": 21, "humidity": 61},
            ]
        )
        result = self.run_jsonl(
            path, "--start-ms", "500", "--end-ms", "2000"
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
        path = self.write_jsonl_objects()
        result = self.run_jsonl(path)
        records = self.assert_success(result)
        self.assertEqual(
            [r["timestamp_ms"] for r in records],
            [0, 1000, 1000, 2000, 3000],
        )
        self.assertEqual(
            [r["elapsed_ms"] for r in records],
            [0, 1000, 1000, 2000, 3000],
        )

    def test_key_order_is_free(self) -> None:
        path = self.write_jsonl(
            ['{"humidity":60,"temperature":20.5,"timestamp_ms":1000}']
        )
        records = self.assert_success(self.run_jsonl(path))
        self.assertEqual(records[0]["timestamp_ms"], 1000)

    def test_negative_decimal_and_exponent_values(self) -> None:
        path = self.write_jsonl(
            ['{"timestamp_ms":7,"temperature":-3.5e2,"humidity":-1}']
        )
        records = self.assert_success(self.run_jsonl(path))
        self.assertEqual(records[0]["temperature"], -350.0)
        self.assertEqual(records[0]["humidity"], -1)

    def test_bom_and_crlf_and_no_final_newline(self) -> None:
        path = self.write_jsonl(
            ['{"timestamp_ms":1,"temperature":2,"humidity":3}'], bom=True
        )
        self.assert_success(self.run_jsonl(path))
        path2 = self.write_jsonl_objects(
            [
                {"timestamp_ms": 1000, "temperature": 20.5, "humidity": 60},
                {"timestamp_ms": 2000, "temperature": 22, "humidity": 62},
            ],
            name="crlf.jsonl",
        )
        path2.write_bytes(
            path2.read_bytes().replace(b"\n", b"\r\n")[:-2]
        )  # 末行无换行
        self.assert_success(self.run_jsonl(path2))

    def test_blank_lines_ignored(self) -> None:
        path = self.write_jsonl(
            [
                "",
                '{"timestamp_ms":1000,"temperature":20.5,"humidity":60}',
                "   ",
                "",
                '{"timestamp_ms":500,"temperature":1,"humidity":2}',
                "\t",
            ]
        )
        records = self.assert_success(self.run_jsonl(path))
        self.assertEqual([r["timestamp_ms"] for r in records], [500, 1000])

    def test_stable_order_keeps_duplicates(self) -> None:
        path = self.write_jsonl_objects(
            [
                {"timestamp_ms": 1000, "temperature": 20.5, "humidity": 60},
                {"timestamp_ms": 1000, "temperature": 21, "humidity": 61},
            ]
        )
        records = self.assert_success(
            self.run_jsonl(path, "--start-ms", "1000", "--end-ms", "1000")
        )
        self.assertEqual([r["temperature"] for r in records], [20.5, 21])


class TestJsonlEmptySuccess(ReplayCliTestCase):
    """空 JSONL、仅空白行或区间无命中时退出 0 且两流为空。"""

    def test_empty_file(self) -> None:
        path = self.write_jsonl([])
        result = self.run_jsonl(path)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "")

    def test_only_blank_lines(self) -> None:
        path = self.write_jsonl(["", "  ", "\t", ""])
        result = self.run_jsonl(path)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "")

    def test_interval_without_hits(self) -> None:
        path = self.write_jsonl_objects()
        result = self.run_jsonl(
            path, "--start-ms", "1500", "--end-ms", "1500"
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "")


class TestJsonlInvalidRecords(ReplayCliTestCase):
    """JSONL 语法/字段/数值错误：退出码 2、空标准输出、报告物理行号。"""

    def assert_jsonl_error(
        self, lines: list[str], line_no: int, fragment: str = ""
    ) -> None:
        path = self.write_jsonl(lines)
        result = self.run_jsonl(path)
        self.assert_input_error(result)
        self.assertIn(f"第 {line_no} 行", result.stderr)
        if fragment:
            self.assertIn(fragment, result.stderr)

    GOOD = '{"timestamp_ms":1000,"temperature":20.5,"humidity":60}'

    def test_acceptance_null_temperature_line_4(self) -> None:
        self.assert_jsonl_error(
            [
                '{"timestamp_ms":2000,"temperature":22,"humidity":62}',
                '{"timestamp_ms":1000,"temperature":20.5,"humidity":60}',
                '{"timestamp_ms":1000,"temperature":21,"humidity":61}',
                '{"timestamp_ms":3000,"temperature":null,"humidity":70}',
            ],
            4,
            "temperature",
        )

    def test_illegal_outside_interval_fails_whole_run(self) -> None:
        path = self.write_jsonl(
            [
                self.GOOD,
                '{"timestamp_ms":9000,"temperature":"x","humidity":1}',
            ]
        )
        result = self.run_jsonl(
            path, "--start-ms", "500", "--end-ms", "2000"
        )
        self.assert_input_error(result)
        self.assertIn("第 2 行", result.stderr)

    def test_blank_lines_count_in_line_numbers(self) -> None:
        self.assert_jsonl_error(
            [self.GOOD, "", '{"timestamp_ms":1,"temperature":null,"humidity":2}'],
            3,
        )
        self.assert_jsonl_error(
            ["   ", '{"oops":1}'], 2
        )

    def test_missing_and_extra_keys(self) -> None:
        self.assert_jsonl_error(
            ['{"timestamp_ms":1,"temperature":2}'], 1, "humidity"
        )
        self.assert_jsonl_error(
            ['{"timestamp_ms":1,"temperature":2,"humidity":3,"x":4}'],
            1,
            "多余键",
        )

    def test_duplicate_key_rejected(self) -> None:
        self.assert_jsonl_error(
            ['{"timestamp_ms":1,"temperature":2,"humidity":3,"humidity":4}'],
            1,
            "重复键",
        )

    def test_timestamp_literal_forms(self) -> None:
        for bad in [
            "-0",
            "-1",
            "1.0",
            "1e3",
            "1E0",
            '"1000"',
            "true",
            "false",
            "null",
        ]:
            self.assert_jsonl_error(
                [f'{{"timestamp_ms":{bad},"temperature":1,"humidity":2}}'],
                1,
            )

    def test_field_value_forms(self) -> None:
        for field, other in (
            ("temperature", '"humidity":2'),
            ("humidity", '"temperature":1'),
        ):
            for bad in [
                '"20"',
                "true",
                "false",
                "null",
                "NaN",
                "Infinity",
                "-Infinity",
                "1e999",
                "-1e999",
                "[1]",
                "{}",
            ]:
                line = (
                    '{"timestamp_ms":1,'
                    + (
                        f'"{field}":{bad},{other}'
                        if field == "temperature"
                        else f'{other},"{field}":{bad}'
                    )
                    + "}"
                )
                self.assert_jsonl_error([line], 1, field)

    def test_non_object_lines(self) -> None:
        for bad in ["[1,2,3]", "123", "12.5", '"str"', "true", "false", "null"]:
            self.assert_jsonl_error([bad], 1)

    def test_syntax_errors(self) -> None:
        for bad in [
            "{",
            "}",
            self.GOOD + " " + self.GOOD,
            '{"timestamp_ms":1 "temperature":2,"humidity":3}',
            "{a:1}",
            '{"timestamp_ms":1,"temperature":1,"humidity":2,}',
        ]:
            self.assert_jsonl_error([bad], 1)

    def test_first_error_reported(self) -> None:
        path = self.write_jsonl([self.GOOD, '{"bad":1}', '{"also":2}'])
        result = self.run_jsonl(path)
        self.assert_input_error(result)
        self.assertIn("第 2 行", result.stderr)


class TestJsonlFileAndOptionErrors(ReplayCliTestCase):
    """格式参数与文件类错误沿用退出码 2 约定。"""

    def test_invalid_format_choice(self) -> None:
        path = self.write_jsonl_objects()
        result = run_replay(path, "--format", "xml")
        self.assert_input_error(result)

    def test_missing_format_value(self) -> None:
        path = self.write_jsonl_objects()
        result = run_replay(path, "--format")
        self.assert_input_error(result)

    def test_missing_file(self) -> None:
        missing = self.tmp_dir / "nope.jsonl"
        result = self.run_jsonl(missing)
        self.assert_input_error(result)

    def test_directory(self) -> None:
        result = self.run_jsonl(self.tmp_dir)
        self.assert_input_error(result)

    def test_invalid_utf8(self) -> None:
        path = self.tmp_dir / "bad.jsonl"
        path.write_bytes(b"\xff\xfe" + b'{"timestamp_ms":1}')
        result = self.run_jsonl(path)
        self.assert_input_error(result)

    def test_no_extension_inference_jsonl_parsed_as_csv(self) -> None:
        # 默认按 csv 解析：jsonl 内容不是合法 CSV 表头，必须失败而非猜测格式。
        path = self.write_jsonl_objects(
            [{"timestamp_ms": 1, "temperature": 2, "humidity": 3}],
            name="weird.dat",
        )
        result = run_replay(path)
        self.assert_input_error(result)

    def test_csv_file_explicit_jsonl_format_fails(self) -> None:
        path = self.write_data_csv()
        result = self.run_jsonl(path)
        self.assert_input_error(result)
        self.assertIn("第 1 行", result.stderr)


if __name__ == "__main__":
    unittest.main()
