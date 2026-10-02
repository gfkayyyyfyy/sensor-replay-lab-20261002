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


if __name__ == "__main__":
    unittest.main()
