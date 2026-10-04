"""区间筛选先于抽样、首个区间内时间戳成为抽样基准的回归测试。

测试自备临时 UTF-8 CSV / JSONL 文件，通过子进程调用公开入口
``python -m sensor_replay``，核对退出码、标准输出与标准错误。固定样本为
四条乱序记录（源文件顺序 2400、900、0、1500）：闭区间筛选先于
``--min-interval-ms`` 抽样生效，区间内最早的时间戳成为抽样基准与
``elapsed_ms`` 起点；末组不足间隔不强行保留；整文件校验先于一切筛选，
区间外的非法记录同样使整次失败。

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

# 固定样本（源文件顺序乱序）。
ROWS = [
    "2400,34,74",
    "900,19,59",
    "0,10,50",
    "1500,25,65",
]
JSONL_RECORDS = [
    {"timestamp_ms": 2400, "temperature": 34, "humidity": 74},
    {"timestamp_ms": 900, "temperature": 19, "humidity": 59},
    {"timestamp_ms": 0, "temperature": 10, "humidity": 50},
    {"timestamp_ms": 1500, "temperature": 25, "humidity": 65},
]

EXPECTED_KEYS = {"timestamp_ms", "elapsed_ms", "temperature", "humidity"}

EMPTY_SUMMARY = {
    "sample_count": 0,
    "first_ms": None,
    "last_ms": None,
    "duration_ms": 0,
    "temperature": {"min": None, "max": None},
    "humidity": {"min": None, "max": None},
}

INTERVAL_ARGS = ["--start-ms", "900", "--end-ms", "2400"]


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


class IntervalBeforeSamplingTestCase(unittest.TestCase):
    """公共基类：在临时目录中准备固定样本的 CSV / JSONL 文件。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp_dir = Path(self._tmp.name)

    def write_csv(self, rows: list[str], name: str = "samples.csv") -> Path:
        path = self.tmp_dir / name
        path.write_text(
            "\n".join([HEADER, *rows]) + "\n", encoding="utf-8"
        )
        return path

    def write_jsonl_objects(
        self, records: list[dict], name: str = "samples.jsonl"
    ) -> Path:
        path = self.tmp_dir / name
        path.write_text(
            "\n".join(json.dumps(r, separators=(",", ":")) for r in records)
            + "\n",
            encoding="utf-8",
        )
        return path

    def run_jsonl(
        self, path: Path, *extra_args: str
    ) -> subprocess.CompletedProcess:
        return run_replay(path, *extra_args, data_format="jsonl")

    @staticmethod
    def parse_records(stdout: str) -> list[dict]:
        return [json.loads(line) for line in stdout.splitlines()]

    def assert_success(
        self, result: subprocess.CompletedProcess
    ) -> list[dict]:
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(result.stderr, "")
        records = self.parse_records(result.stdout)
        for record in records:
            self.assertEqual(set(record.keys()), EXPECTED_KEYS)
        return records

    def assert_input_error(self, result: subprocess.CompletedProcess) -> None:
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertNotEqual(result.stderr, "")
        self.assertNotIn("Traceback", result.stderr)


class TestIntervalBeforeSampling(IntervalBeforeSamplingTestCase):
    """区间筛选先于抽样：首个区间内时间戳成为抽样基准。"""

    def test_min_interval_only_csv(self) -> None:
        # 无区间时基准为全文件最早的 0；1500 与 0 相差 1500 >= 1000 保留。
        path = self.write_csv(ROWS)
        records = self.assert_success(
            run_replay(path, "--min-interval-ms", "1000")
        )
        self.assertEqual(
            [r["timestamp_ms"] for r in records], [0, 1500]
        )

    def test_min_interval_only_jsonl(self) -> None:
        path = self.write_jsonl_objects(JSONL_RECORDS)
        records = self.assert_success(
            self.run_jsonl(path, "--min-interval-ms", "1000")
        )
        self.assertEqual(
            [r["timestamp_ms"] for r in records], [0, 1500]
        )

    def test_interval_then_sampling_csv(self) -> None:
        # 区间 [900,2400] 先筛出 900、1500、2400；基准为区间内最早的
        # 900；1500 与 900 相差 600 被跳过；2400 与 900 相差 1500 保留。
        path = self.write_csv(ROWS)
        records = self.assert_success(
            run_replay(
                path, "--min-interval-ms", "1000", *INTERVAL_ARGS
            )
        )
        self.assertEqual(
            [r["timestamp_ms"] for r in records], [900, 2400]
        )
        self.assertEqual(
            [r["elapsed_ms"] for r in records], [0, 1500]
        )
        self.assertEqual(
            [(r["temperature"], r["humidity"]) for r in records],
            [(19, 59), (34, 74)],
        )

    def test_interval_then_sampling_jsonl_identical(self) -> None:
        csv_path = self.write_csv(ROWS)
        jsonl_path = self.write_jsonl_objects(JSONL_RECORDS)
        csv_result = run_replay(
            csv_path, "--min-interval-ms", "1000", *INTERVAL_ARGS
        )
        jsonl_result = self.run_jsonl(
            jsonl_path, "--min-interval-ms", "1000", *INTERVAL_ARGS
        )
        records = self.assert_success(jsonl_result)
        self.assertEqual(
            [r["timestamp_ms"] for r in records], [900, 2400]
        )
        self.assertEqual(
            [r["elapsed_ms"] for r in records], [0, 1500]
        )
        self.assertEqual(
            [(r["temperature"], r["humidity"]) for r in records],
            [(19, 59), (34, 74)],
        )
        # 两种格式的回放输出逐字节一致。
        self.assertEqual(jsonl_result.stdout, csv_result.stdout)


class TestIntervalSummary(IntervalBeforeSamplingTestCase):
    """对区间 [900,2400] 启用 --summary：统计抽样后的最终记录。"""

    def test_summary_csv(self) -> None:
        path = self.write_csv(ROWS)
        result = run_replay(
            path,
            "--summary",
            "--min-interval-ms",
            "1000",
            *INTERVAL_ARGS,
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(result.stderr, "")
        # 唯一一行摘要，末尾保留换行。
        self.assertTrue(result.stdout.endswith("\n"))
        self.assertEqual(len(result.stdout.splitlines()), 1)
        self.assertEqual(
            json.loads(result.stdout),
            {
                "sample_count": 2,
                "first_ms": 900,
                "last_ms": 2400,
                "duration_ms": 1500,
                "temperature": {"min": 19, "max": 34},
                "humidity": {"min": 59, "max": 74},
            },
        )

    def test_summary_jsonl_identical(self) -> None:
        csv_path = self.write_csv(ROWS)
        jsonl_path = self.write_jsonl_objects(JSONL_RECORDS)
        csv_result = run_replay(
            csv_path, "--summary", "--min-interval-ms", "1000",
            *INTERVAL_ARGS,
        )
        jsonl_result = self.run_jsonl(
            jsonl_path, "--summary", "--min-interval-ms", "1000",
            *INTERVAL_ARGS,
        )
        self.assertEqual(jsonl_result.returncode, 0, msg=jsonl_result.stderr)
        self.assertEqual(jsonl_result.stdout, csv_result.stdout)


class TestBoundaryIntervals(IntervalBeforeSamplingTestCase):
    """同一规则的边界：终点不强行保留、单点区间、空区间。"""

    def test_end_point_not_forced(self) -> None:
        # 区间 [900,1500] 筛出 900、1500；1500 与基准 900 相差 600，
        # 不能因处在区间终点而强行保留。
        path = self.write_csv(ROWS)
        records = self.assert_success(
            run_replay(
                path,
                "--min-interval-ms",
                "1000",
                "--start-ms",
                "900",
                "--end-ms",
                "1500",
            )
        )
        self.assertEqual([r["timestamp_ms"] for r in records], [900])
        self.assertEqual([r["elapsed_ms"] for r in records], [0])

    def test_single_point_interval(self) -> None:
        # 区间 [1500,1500] 只含该样本，保留且 elapsed_ms 为 0。
        path = self.write_csv(ROWS)
        records = self.assert_success(
            run_replay(
                path,
                "--min-interval-ms",
                "1000",
                "--start-ms",
                "1500",
                "--end-ms",
                "1500",
            )
        )
        self.assertEqual([r["timestamp_ms"] for r in records], [1500])
        self.assertEqual([r["elapsed_ms"] for r in records], [0])
        self.assertEqual(
            [(r["temperature"], r["humidity"]) for r in records],
            [(25, 65)],
        )

    def test_empty_interval_replay(self) -> None:
        # 区间 [901,1499] 不含任何样本：回放为空，退出码 0。
        path = self.write_csv(ROWS)
        result = run_replay(
            path,
            "--min-interval-ms",
            "1000",
            "--start-ms",
            "901",
            "--end-ms",
            "1499",
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "")

    def test_empty_interval_summary(self) -> None:
        # 空区间的摘要：数量与跨度为 0，首末时间戳与各通道极值为 null。
        path = self.write_csv(ROWS)
        result = run_replay(
            path,
            "--summary",
            "--min-interval-ms",
            "1000",
            "--start-ms",
            "901",
            "--end-ms",
            "1499",
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertTrue(result.stdout.endswith("\n"))
        self.assertEqual(json.loads(result.stdout), EMPTY_SUMMARY)


class TestInvalidRecordOutsideInterval(IntervalBeforeSamplingTestCase):
    """区间外的非法记录仍使整次失败：整文件校验先于区间筛选与抽样。"""

    # 源文件第三条样本（0,10,50）落在区间 [900,2400] 之外。

    def test_csv_nan_replay(self) -> None:
        rows = ["2400,34,74", "900,19,59", "0,NaN,50", "1500,25,65"]
        path = self.write_csv(rows)
        result = run_replay(
            path, "--min-interval-ms", "1000", *INTERVAL_ARGS
        )
        self.assert_input_error(result)
        self.assertIn("temperature", result.stderr)
        self.assertIn("第 4 条 CSV 记录", result.stderr)

    def test_csv_nan_summary(self) -> None:
        rows = ["2400,34,74", "900,19,59", "0,NaN,50", "1500,25,65"]
        path = self.write_csv(rows)
        result = run_replay(
            path, "--summary", "--min-interval-ms", "1000", *INTERVAL_ARGS
        )
        self.assert_input_error(result)
        self.assertIn("temperature", result.stderr)
        self.assertIn("第 4 条 CSV 记录", result.stderr)

    def test_jsonl_null_replay(self) -> None:
        records = [
            {"timestamp_ms": 2400, "temperature": 34, "humidity": 74},
            {"timestamp_ms": 900, "temperature": 19, "humidity": 59},
            {"timestamp_ms": 0, "temperature": None, "humidity": 50},
            {"timestamp_ms": 1500, "temperature": 25, "humidity": 65},
        ]
        path = self.write_jsonl_objects(records)
        result = self.run_jsonl(
            path, "--min-interval-ms", "1000", *INTERVAL_ARGS
        )
        self.assert_input_error(result)
        self.assertIn("temperature", result.stderr)
        self.assertIn("第 3 行", result.stderr)

    def test_jsonl_null_summary(self) -> None:
        records = [
            {"timestamp_ms": 2400, "temperature": 34, "humidity": 74},
            {"timestamp_ms": 900, "temperature": 19, "humidity": 59},
            {"timestamp_ms": 0, "temperature": None, "humidity": 50},
            {"timestamp_ms": 1500, "temperature": 25, "humidity": 65},
        ]
        path = self.write_jsonl_objects(records)
        result = self.run_jsonl(
            path, "--summary", "--min-interval-ms", "1000", *INTERVAL_ARGS
        )
        self.assert_input_error(result)
        self.assertIn("temperature", result.stderr)
        self.assertIn("第 3 行", result.stderr)


if __name__ == "__main__":
    unittest.main()
