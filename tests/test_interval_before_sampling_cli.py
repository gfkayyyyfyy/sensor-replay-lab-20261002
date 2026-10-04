"""区间筛选先于最小间隔抽样的命令行回归测试。

测试自备临时 UTF-8 文件：CSV 使用默认格式（不传 --format），JSONL 显式
指定 ``--format jsonl``，两者字段与取值完全相同。通过子进程调用公开入口
``python -m sensor_replay``，核对退出码、标准输出与标准错误。

固定样本（源文件顺序刻意乱序）::

    timestamp_ms,temperature,humidity
    2400,34,74
    900,19,59
    0,10,50
    1500,25,65

本组测试锁定的既有行为：处理顺序为整文件校验 → 闭区间筛选 → 稳定排序 →
最小间隔抽样。只给 ``--min-interval-ms 1000`` 时最早时间戳 0 成为抽样基准，
保留 0 与 1500；加入 ``--start-ms 900 --end-ms 2400`` 后，区间内最早的
900 成为抽样基准（区间外的 0 不参与），900 与 2400 相差恰好 1500 均保留，
elapsed_ms 从 900 重新计起。终点处的末组不足间隔时不强行保留。整文件校验
先于筛选：区间外的非法温度同样使回放与摘要以退出码 2 失败。

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

INTERVAL_FLAG = "--min-interval-ms"

# 固定样本：源顺序为 2400、900、0、1500（乱序写入，排序由产品完成）。
FIXED_ROWS = [
    "2400,34,74",
    "900,19,59",
    "0,10,50",
    "1500,25,65",
]
FIXED_JSONL = [
    {"timestamp_ms": 2400, "temperature": 34, "humidity": 74},
    {"timestamp_ms": 900, "temperature": 19, "humidity": 59},
    {"timestamp_ms": 0, "temperature": 10, "humidity": 50},
    {"timestamp_ms": 1500, "temperature": 25, "humidity": 65},
]

# 第三条源样本（timestamp_ms=0）温度非法时的变体：CSV 用 NaN，JSONL 用 null。
BAD_TEMPERATURE_ROWS = [
    "2400,34,74",
    "900,19,59",
    "0,NaN,50",
    "1500,25,65",
]
BAD_TEMPERATURE_JSONL = [
    {"timestamp_ms": 2400, "temperature": 34, "humidity": 74},
    {"timestamp_ms": 900, "temperature": 19, "humidity": 59},
    {"timestamp_ms": 0, "temperature": None, "humidity": 50},
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


def run_replay(
    path: Path, *extra_args: str, data_format: str | None = None
) -> subprocess.CompletedProcess:
    """以子进程运行 ``python -m sensor_replay`` 并捕获结果。

    data_format 为 None 时不传 --format（走 CSV 默认）；为 "jsonl" 时显式
    传入 --format jsonl。
    """
    args = [sys.executable, "-m", "sensor_replay", str(path)]
    if data_format is not None:
        args.extend(["--format", data_format])
    args.extend(extra_args)
    return subprocess.run(
        args,
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


class IntervalSamplingTestCase(unittest.TestCase):
    """公共基类：在临时目录中准备 CSV / JSONL 文件并发起子进程。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp_dir = Path(self._tmp.name)

    def write_csv(self, lines: list[str], name: str = "samples.csv") -> Path:
        path = self.tmp_dir / name
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
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

    def fixed_files(self) -> tuple[Path, Path]:
        return (
            self.write_csv([HEADER, *FIXED_ROWS]),
            self.write_jsonl_objects(FIXED_JSONL),
        )

    def run_csv(self, path: Path, *extra_args: str) -> subprocess.CompletedProcess:
        # CSV 走默认格式：不传 --format。
        return run_replay(path, *extra_args)

    def run_jsonl(
        self, path: Path, *extra_args: str
    ) -> subprocess.CompletedProcess:
        return run_replay(path, *extra_args, data_format="jsonl")

    @staticmethod
    def parse_records(stdout: str) -> list[dict]:
        return [json.loads(line) for line in stdout.splitlines()]

    def assert_success(self, result: subprocess.CompletedProcess) -> list[dict]:
        """成功路径：退出码 0、标准错误为空；返回解析后的输出记录。"""
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(result.stderr, "")
        records = self.parse_records(result.stdout)
        for record in records:
            # 每条只含既有四个字段（无 missing_before 等额外字段）。
            self.assertEqual(set(record.keys()), EXPECTED_KEYS)
        return records

    def assert_summary_success(
        self, result: subprocess.CompletedProcess, expected: dict
    ) -> dict:
        """摘要成功路径：退出码 0、标准错误空、唯一 JSON 对象加末尾换行。"""
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertTrue(
            result.stdout.endswith("\n"), msg="摘要必须保留末尾换行"
        )
        body = result.stdout[:-1]
        self.assertNotIn("\n", body, msg="摘要只能有一个 JSON 对象")
        summary = json.loads(body)
        self.assertEqual(summary, expected)
        return summary

    def assert_input_error(
        self, result: subprocess.CompletedProcess, location: str
    ) -> None:
        """退出码 2、标准输出为空；标准错误点名 temperature 与既有定位、无堆栈。"""
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertNotEqual(result.stderr, "")
        self.assertIn("temperature", result.stderr)
        self.assertIn(location, result.stderr)
        self.assertNotIn("Traceback", result.stderr)


class TestMinIntervalWithoutRange(IntervalSamplingTestCase):
    """只给 --min-interval-ms 1000：全文件最早时间戳 0 是抽样基准。"""

    EXPECTED = [
        {"timestamp_ms": 0, "elapsed_ms": 0, "temperature": 10, "humidity": 50},
        {"timestamp_ms": 1500, "elapsed_ms": 1500, "temperature": 25, "humidity": 65},
    ]

    def test_csv_default_format(self) -> None:
        csv_path, _ = self.fixed_files()
        records = self.assert_success(
            self.run_csv(csv_path, INTERVAL_FLAG, "1000")
        )
        self.assertEqual(records, self.EXPECTED)

    def test_jsonl_explicit_format_matches(self) -> None:
        _, jsonl_path = self.fixed_files()
        result = self.run_jsonl(jsonl_path, INTERVAL_FLAG, "1000")
        records = self.assert_success(result)
        self.assertEqual(records, self.EXPECTED)

    def test_two_formats_emit_identical_stdout(self) -> None:
        csv_path, jsonl_path = self.fixed_files()
        csv_result = self.run_csv(csv_path, INTERVAL_FLAG, "1000")
        jsonl_result = self.run_jsonl(jsonl_path, INTERVAL_FLAG, "1000")
        self.assertEqual(csv_result.returncode, 0)
        self.assertEqual(jsonl_result.returncode, 0)
        self.assertEqual(jsonl_result.stdout, csv_result.stdout)

    def test_skipped_groups_do_not_move_anchor(self) -> None:
        # 900 距基准 0 不足 1000 被跳过且不移动基准；1500 距基准 0 为
        # 1500 >= 1000 保留；2400 距新基准 1500 仅 900，作为末组不强行保留。
        csv_path, _ = self.fixed_files()
        records = self.assert_success(
            self.run_csv(csv_path, INTERVAL_FLAG, "1000")
        )
        self.assertEqual(
            [r["timestamp_ms"] for r in records], [0, 1500]
        )


class TestIntervalFilteringBeforeSampling(IntervalSamplingTestCase):
    """[900,2400]：区间筛选先于抽样，区间内首个时间戳 900 成为基准。"""

    EXPECTED = [
        {"timestamp_ms": 900, "elapsed_ms": 0, "temperature": 19, "humidity": 59},
        {"timestamp_ms": 2400, "elapsed_ms": 1500, "temperature": 34, "humidity": 74},
    ]
    RANGE_ARGS = ("--start-ms", "900", "--end-ms", "2400")

    EXPECTED_SUMMARY = {
        "sample_count": 2,
        "first_ms": 900,
        "last_ms": 2400,
        "duration_ms": 1500,
        "temperature": {"min": 19, "max": 34},
        "humidity": {"min": 59, "max": 74},
    }

    def test_csv_interval_anchors_sampling_at_first_in_range(self) -> None:
        csv_path, _ = self.fixed_files()
        records = self.assert_success(
            self.run_csv(csv_path, INTERVAL_FLAG, "1000", *self.RANGE_ARGS)
        )
        self.assertEqual(records, self.EXPECTED)

    def test_jsonl_interval_matches_csv(self) -> None:
        _, jsonl_path = self.fixed_files()
        records = self.assert_success(
            self.run_jsonl(jsonl_path, INTERVAL_FLAG, "1000", *self.RANGE_ARGS)
        )
        self.assertEqual(records, self.EXPECTED)

    def test_filtered_result_differs_from_unfiltered(self) -> None:
        # 直接锁定“筛选先于抽样”：同一文件不筛选时基准是 0（输出
        # 0,1500）；筛选后基准改为区间内首个 900（输出 900,2400）。
        csv_path, jsonl_path = self.fixed_files()
        for runner, path in (
            (self.run_csv, csv_path),
            (self.run_jsonl, jsonl_path),
        ):
            unfiltered = self.assert_success(
                runner(path, INTERVAL_FLAG, "1000")
            )
            filtered = self.assert_success(
                runner(path, INTERVAL_FLAG, "1000", *self.RANGE_ARGS)
            )
            self.assertEqual(
                [r["timestamp_ms"] for r in unfiltered], [0, 1500]
            )
            self.assertEqual(
                [r["timestamp_ms"] for r in filtered], [900, 2400]
            )

    def test_two_formats_emit_identical_stdout(self) -> None:
        csv_path, jsonl_path = self.fixed_files()
        csv_result = self.run_csv(
            csv_path, INTERVAL_FLAG, "1000", *self.RANGE_ARGS
        )
        jsonl_result = self.run_jsonl(
            jsonl_path, INTERVAL_FLAG, "1000", *self.RANGE_ARGS
        )
        self.assertEqual(csv_result.returncode, 0)
        self.assertEqual(jsonl_result.returncode, 0)
        self.assertEqual(jsonl_result.stdout, csv_result.stdout)

    def test_csv_summary_after_sampling(self) -> None:
        csv_path, _ = self.fixed_files()
        result = self.run_csv(
            csv_path, "--summary", INTERVAL_FLAG, "1000", *self.RANGE_ARGS
        )
        self.assert_summary_success(result, self.EXPECTED_SUMMARY)

    def test_jsonl_summary_matches_csv(self) -> None:
        csv_path, jsonl_path = self.fixed_files()
        csv_result = self.run_csv(
            csv_path, "--summary", INTERVAL_FLAG, "1000", *self.RANGE_ARGS
        )
        jsonl_result = self.run_jsonl(
            jsonl_path, "--summary", INTERVAL_FLAG, "1000", *self.RANGE_ARGS
        )
        self.assertEqual(jsonl_result.returncode, 0)
        self.assertEqual(jsonl_result.stdout, csv_result.stdout)
        self.assert_summary_success(jsonl_result, self.EXPECTED_SUMMARY)


class TestClosedIntervalBoundaries(IntervalSamplingTestCase):
    """闭区间边界规则：终点不强行保留；单点区间保留；空隙区间为空。"""

    def test_end_point_group_below_interval_not_forced_csv(self) -> None:
        # [900,1500]：900 为基准保留；1500 虽处在终点且为末组，但与基准
        # 仅差 600 < 1000，不强行保留。
        csv_path, _ = self.fixed_files()
        records = self.assert_success(
            self.run_csv(
                csv_path,
                INTERVAL_FLAG,
                "1000",
                "--start-ms",
                "900",
                "--end-ms",
                "1500",
            )
        )
        self.assertEqual(
            records,
            [
                {
                    "timestamp_ms": 900,
                    "elapsed_ms": 0,
                    "temperature": 19,
                    "humidity": 59,
                }
            ],
        )

    def test_end_point_group_below_interval_not_forced_jsonl(self) -> None:
        _, jsonl_path = self.fixed_files()
        result = self.run_jsonl(
            jsonl_path,
            INTERVAL_FLAG,
            "1000",
            "--start-ms",
            "900",
            "--end-ms",
            "1500",
        )
        records = self.assert_success(result)
        self.assertEqual([r["timestamp_ms"] for r in records], [900])
        self.assertEqual(records[0]["elapsed_ms"], 0)
        # 1500 不得出现在输出中。
        self.assertNotIn(1500, [r["timestamp_ms"] for r in records])

    def test_single_point_interval_kept_csv(self) -> None:
        # [1500,1500]：唯一样本既是首组也是末组，保留且 elapsed_ms 为 0。
        csv_path, _ = self.fixed_files()
        records = self.assert_success(
            self.run_csv(
                csv_path,
                INTERVAL_FLAG,
                "1000",
                "--start-ms",
                "1500",
                "--end-ms",
                "1500",
            )
        )
        self.assertEqual(
            records,
            [
                {
                    "timestamp_ms": 1500,
                    "elapsed_ms": 0,
                    "temperature": 25,
                    "humidity": 65,
                }
            ],
        )

    def test_single_point_interval_kept_jsonl(self) -> None:
        _, jsonl_path = self.fixed_files()
        result = self.run_jsonl(
            jsonl_path,
            INTERVAL_FLAG,
            "1000",
            "--start-ms",
            "1500",
            "--end-ms",
            "1500",
        )
        records = self.assert_success(result)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["timestamp_ms"], 1500)
        self.assertEqual(records[0]["elapsed_ms"], 0)
        self.assertEqual(
            (records[0]["temperature"], records[0]["humidity"]), (25, 65)
        )

    def test_gap_interval_replay_empty_csv(self) -> None:
        # [901,1499] 不含任何样本：回放为空，退出码 0、标准错误为空。
        csv_path, _ = self.fixed_files()
        result = self.run_csv(
            csv_path,
            INTERVAL_FLAG,
            "1000",
            "--start-ms",
            "901",
            "--end-ms",
            "1499",
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "")

    def test_gap_interval_replay_empty_jsonl(self) -> None:
        _, jsonl_path = self.fixed_files()
        result = self.run_jsonl(
            jsonl_path,
            INTERVAL_FLAG,
            "1000",
            "--start-ms",
            "901",
            "--end-ms",
            "1499",
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "")

    def test_gap_interval_summary_empty_csv(self) -> None:
        csv_path, _ = self.fixed_files()
        result = self.run_csv(
            csv_path,
            "--summary",
            INTERVAL_FLAG,
            "1000",
            "--start-ms",
            "901",
            "--end-ms",
            "1499",
        )
        self.assert_summary_success(result, EMPTY_SUMMARY)

    def test_gap_interval_summary_empty_jsonl(self) -> None:
        _, jsonl_path = self.fixed_files()
        result = self.run_jsonl(
            jsonl_path,
            "--summary",
            INTERVAL_FLAG,
            "1000",
            "--start-ms",
            "901",
            "--end-ms",
            "1499",
        )
        self.assert_summary_success(result, EMPTY_SUMMARY)


class TestInvalidRecordOutsideIntervalFails(IntervalSamplingTestCase):
    """区间外的非法温度仍先经整文件校验：回放与摘要都以退出码 2 失败。

    被改坏的是第三条源样本（timestamp_ms=0），位于所选区间 [900,2400]
    之外。CSV 中它是第 4 条 CSV 记录（表头算第 1 条）；JSONL 中它是
    第 3 行。
    """

    RANGE_ARGS = (
        INTERVAL_FLAG,
        "1000",
        "--start-ms",
        "900",
        "--end-ms",
        "2400",
    )

    def test_csv_nan_outside_range_fails_replay(self) -> None:
        path = self.write_csv([HEADER, *BAD_TEMPERATURE_ROWS])
        result = self.run_csv(path, *self.RANGE_ARGS)
        self.assert_input_error(result, "第 4 条 CSV 记录")

    def test_csv_nan_outside_range_fails_summary(self) -> None:
        path = self.write_csv([HEADER, *BAD_TEMPERATURE_ROWS])
        result = self.run_csv(path, "--summary", *self.RANGE_ARGS)
        self.assert_input_error(result, "第 4 条 CSV 记录")

    def test_jsonl_null_outside_range_fails_replay(self) -> None:
        path = self.write_jsonl_objects(BAD_TEMPERATURE_JSONL)
        result = self.run_jsonl(path, *self.RANGE_ARGS)
        self.assert_input_error(result, "第 3 行")

    def test_jsonl_null_outside_range_fails_summary(self) -> None:
        path = self.write_jsonl_objects(BAD_TEMPERATURE_JSONL)
        result = self.run_jsonl(path, "--summary", *self.RANGE_ARGS)
        self.assert_input_error(result, "第 3 行")


if __name__ == "__main__":
    unittest.main()
