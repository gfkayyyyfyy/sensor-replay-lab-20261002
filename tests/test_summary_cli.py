"""``--summary`` 统计摘要开关的命令行回归测试。

测试自备临时 UTF-8 文件（CSV 与 JSONL），通过子进程调用公开入口
``python -m sensor_replay``，核对退出码、标准输出（唯一 JSON 对象加末尾
换行）与标准错误。启用 --summary 时不回放，仅输出六个摘要字段；省略时
输出行为由其余测试覆盖，本文件不再重复。

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

# 验收数据：源顺序为 1000,20,60 / 0,10,50 / 1000,30,70（乱序且重复）。
ACCEPTANCE_ROWS = [
    "1000,20,60",
    "0,10,50",
    "1000,30,70",
]
ACCEPTANCE_JSONL = [
    {"timestamp_ms": 1000, "temperature": 20, "humidity": 60},
    {"timestamp_ms": 0, "temperature": 10, "humidity": 50},
    {"timestamp_ms": 1000, "temperature": 30, "humidity": 70},
]

EMPTY_SUMMARY = {
    "sample_count": 0,
    "first_ms": None,
    "last_ms": None,
    "duration_ms": 0,
    "temperature": {"min": None, "max": None},
    "humidity": {"min": None, "max": None},
}

SUMMARY_KEYS = {
    "sample_count",
    "first_ms",
    "last_ms",
    "duration_ms",
    "temperature",
    "humidity",
}


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


class SummaryCliTestCase(unittest.TestCase):
    """公共基类：在临时目录中准备 CSV / JSONL 文件。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp_dir = Path(self._tmp.name)

    def write_csv(self, lines: list[str], name: str = "samples.csv") -> Path:
        path = self.tmp_dir / name
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path

    def write_jsonl(
        self, lines: list[str], name: str = "samples.jsonl"
    ) -> Path:
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

    def assert_summary_success(
        self, result: subprocess.CompletedProcess, expected: dict
    ) -> dict:
        """断言摘要成功路径并返回解析后的摘要对象。

        退出码 0、标准错误为空；标准输出恰好是一个 JSON 对象加单个末尾
        换行（无第二个 JSON 行、无多余空白）。
        """
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertTrue(
            result.stdout.endswith("\n"), msg="标准输出必须以换行结尾"
        )
        # 去掉唯一一个末尾换行后整体必须是一个 JSON 对象。
        body = result.stdout[:-1]
        self.assertNotIn("\n", body, msg="摘要只能有一个 JSON 对象")
        summary = json.loads(body)
        self.assertIsInstance(summary, dict)
        self.assertEqual(set(summary.keys()), SUMMARY_KEYS)
        self.assertEqual(
            set(summary["temperature"].keys()), {"min", "max"}
        )
        self.assertEqual(set(summary["humidity"].keys()), {"min", "max"})
        self.assertEqual(summary, expected)
        return summary

    def assert_input_error(self, result: subprocess.CompletedProcess) -> None:
        """退出码 2、标准输出为空、标准错误非空且无堆栈。"""
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertNotEqual(result.stderr, "")
        self.assertNotIn("Traceback", result.stderr)


class TestSummaryAcceptance(SummaryCliTestCase):
    """验收场景：first 下数量 2、跨度 1000、极值取保留样本。"""

    EXPECTED_FIRST = {
        "sample_count": 2,
        "first_ms": 0,
        "last_ms": 1000,
        "duration_ms": 1000,
        "temperature": {"min": 10, "max": 20},
        "humidity": {"min": 50, "max": 60},
    }

    def test_csv_acceptance_first(self) -> None:
        path = self.write_csv([HEADER, *ACCEPTANCE_ROWS])
        result = run_replay(path, "--summary", "--duplicate-policy", "first")
        self.assert_summary_success(result, self.EXPECTED_FIRST)

    def test_jsonl_acceptance_first(self) -> None:
        path = self.write_jsonl_objects(ACCEPTANCE_JSONL)
        result = run_replay(
            path,
            "--summary",
            "--duplicate-policy",
            "first",
            data_format="jsonl",
        )
        self.assert_summary_success(result, self.EXPECTED_FIRST)

    def test_all_counts_duplicates_separately(self) -> None:
        path = self.write_csv([HEADER, *ACCEPTANCE_ROWS])
        result = run_replay(path, "--summary")
        self.assert_summary_success(
            result,
            {
                "sample_count": 3,
                "first_ms": 0,
                "last_ms": 1000,
                "duration_ms": 1000,
                "temperature": {"min": 10, "max": 30},
                "humidity": {"min": 50, "max": 70},
            },
        )

    def test_last_keeps_final_complete_sample(self) -> None:
        path = self.write_csv([HEADER, *ACCEPTANCE_ROWS])
        result = run_replay(path, "--summary", "--duplicate-policy", "last")
        self.assert_summary_success(
            result,
            {
                "sample_count": 2,
                "first_ms": 0,
                "last_ms": 1000,
                "duration_ms": 1000,
                "temperature": {"min": 10, "max": 30},
                "humidity": {"min": 50, "max": 70},
            },
        )

    def test_single_record_span_zero_and_extremes_equal(self) -> None:
        path = self.write_csv([HEADER, "42,7.5,9"])
        result = run_replay(path, "--summary")
        self.assert_summary_success(
            result,
            {
                "sample_count": 1,
                "first_ms": 42,
                "last_ms": 42,
                "duration_ms": 0,
                "temperature": {"min": 7.5, "max": 7.5},
                "humidity": {"min": 9, "max": 9},
            },
        )

    def test_extremes_come_from_real_samples_not_average(self) -> None:
        # first 保留 0 与 5000 两个完整样本；极值即这两条的温湿度。
        path = self.write_csv(
            [
                HEADER,
                "5000,40,80",
                "0,-5,10",
                "5000,0,0",  # first 会舍弃，不参与极值
            ]
        )
        result = run_replay(path, "--summary", "--duplicate-policy", "first")
        summary = self.assert_summary_success(
            result,
            {
                "sample_count": 2,
                "first_ms": 0,
                "last_ms": 5000,
                "duration_ms": 5000,
                "temperature": {"min": -5, "max": 40},
                "humidity": {"min": 10, "max": 80},
            },
        )
        # 明确不出现被舍弃样本或平均值。
        self.assertEqual(summary["temperature"]["min"], -5)


class TestSummaryBoundsAndExtremes(SummaryCliTestCase):
    """时间戳两端取最终记录而非参数边界；极值只取真实样本。"""

    def test_endpoints_are_selected_records_not_bounds(self) -> None:
        path = self.write_csv(
            [HEADER, "0,1,1", "1000,2,2", "4000,3,3", "9000,4,4"]
        )
        result = run_replay(
            path,
            "--summary",
            "--start-ms",
            "500",
            "--end-ms",
            "5000",
            "--duplicate-policy",
            "all",
        )
        self.assert_summary_success(
            result,
            {
                "sample_count": 2,
                "first_ms": 1000,
                "last_ms": 4000,
                "duration_ms": 3000,
                "temperature": {"min": 2, "max": 3},
                "humidity": {"min": 2, "max": 3},
            },
        )

    def test_decimal_extremes_preserved(self) -> None:
        path = self.write_csv(
            [
                HEADER,
                "10,-3.5,40.25",
                "20,21.75,55.5",
            ]
        )
        result = run_replay(path, "--summary")
        summary = json.loads(result.stdout)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(summary["temperature"], {"min": -3.5, "max": 21.75})
        self.assertEqual(summary["humidity"], {"min": 40.25, "max": 55.5})


class TestSummaryEmptySelection(SummaryCliTestCase):
    """无选中记录：仍输出摘要，数量跨度为 0，其余为 null。"""

    def test_header_only_csv(self) -> None:
        path = self.write_csv([HEADER])
        result = run_replay(path, "--summary")
        self.assert_summary_success(result, EMPTY_SUMMARY)

    def test_empty_jsonl(self) -> None:
        path = self.write_jsonl([])
        result = run_replay(path, "--summary", data_format="jsonl")
        self.assert_summary_success(result, EMPTY_SUMMARY)

    def test_blank_only_jsonl(self) -> None:
        path = self.write_jsonl(["", "  ", "\t", ""])
        result = run_replay(path, "--summary", data_format="jsonl")
        self.assert_summary_success(result, EMPTY_SUMMARY)

    def test_interval_without_hits(self) -> None:
        path = self.write_csv([HEADER, *ACCEPTANCE_ROWS])
        result = run_replay(
            path,
            "--summary",
            "--start-ms",
            "9000",
            "--end-ms",
            "9999",
        )
        self.assert_summary_success(result, EMPTY_SUMMARY)

    def test_dedup_leaves_no_hit_outside_single_point(self) -> None:
        # first/last 不改变记录集合时区间仍无命中：正常输出空摘要。
        path = self.write_csv([HEADER, *ACCEPTANCE_ROWS])
        result = run_replay(
            path,
            "--summary",
            "--duplicate-policy",
            "last",
            "--start-ms",
            "5000",
        )
        self.assert_summary_success(result, EMPTY_SUMMARY)

    def test_empty_csv_still_errors(self) -> None:
        # 空 CSV（连表头都没有）即使带 --summary 也不产生摘要。
        path = self.write_csv([""])
        result = run_replay(path, "--summary")
        self.assert_input_error(result)
        self.assertIn("第 1 条 CSV 记录", result.stderr)


class TestSummaryWithGapThreshold(SummaryCliTestCase):
    """--gap-threshold-ms 可同时使用：继续校验，合法值不改变摘要。"""

    def test_valid_threshold_does_not_change_summary(self) -> None:
        path = self.write_csv([HEADER, *ACCEPTANCE_ROWS])
        without = run_replay(path, "--summary")
        with_gap = run_replay(path, "--summary", "--gap-threshold-ms", "1")
        self.assertEqual(with_gap.returncode, 0)
        self.assertEqual(with_gap.stdout, without.stdout)

    def test_invalid_threshold_still_rejected(self) -> None:
        path = self.write_csv([HEADER, *ACCEPTANCE_ROWS])
        for value in ("0", "00", "-1", "1.5", "1e3", ""):
            result = run_replay(
                path,
                "--summary",
                "--gap-threshold-ms",
                value,
            )
            self.assert_input_error(result)
            self.assertIn("--gap-threshold-ms", result.stderr)

    def test_threshold_missing_value_rejected(self) -> None:
        path = self.write_csv([HEADER, *ACCEPTANCE_ROWS])
        result = run_replay(path, "--summary", "--gap-threshold-ms")
        self.assert_input_error(result)


class TestSummaryInvalidInputs(SummaryCliTestCase):
    """非法文件、参数或样本：退出码 2、空标准输出、保留既有定位且无摘要。"""

    def test_invalid_record_outside_interval_no_summary(self) -> None:
        path = self.write_csv(
            [HEADER, *ACCEPTANCE_ROWS, "9000,NaN,70"]
        )
        result = run_replay(
            path,
            "--summary",
            "--start-ms",
            "0",
            "--end-ms",
            "1000",
        )
        self.assert_input_error(result)
        self.assertIn("第 5 条 CSV 记录", result.stderr)

    def test_dropped_duplicate_invalid_no_summary(self) -> None:
        # first 会舍弃后出现的 1000,1e999,51，但整文件校验仍先失败。
        path = self.write_csv(
            [
                HEADER,
                "1000,20,50",
                "0,19,49",
                "1000,1e999,51",
                "2000,22,52",
            ]
        )
        result = run_replay(path, "--summary", "--duplicate-policy", "first")
        self.assert_input_error(result)
        self.assertIn("第 4 条 CSV 记录", result.stderr)
        self.assertIn("temperature", result.stderr)

    def test_jsonl_invalid_record_reports_line(self) -> None:
        path = self.write_jsonl(
            [
                '{"timestamp_ms":1,"temperature":2,"humidity":3}',
                '{"timestamp_ms":9,"temperature":"x","humidity":8}',
            ]
        )
        result = run_replay(
            path,
            "--summary",
            "--start-ms",
            "1",
            "--end-ms",
            "1",
            data_format="jsonl",
        )
        self.assert_input_error(result)
        self.assertIn("第 2 行", result.stderr)

    def test_invalid_bounds_no_summary(self) -> None:
        path = self.write_csv([HEADER, *ACCEPTANCE_ROWS])
        result = run_replay(
            path, "--summary", "--start-ms", "2000", "--end-ms", "1000"
        )
        self.assert_input_error(result)

    def test_missing_file_no_summary(self) -> None:
        missing = self.tmp_dir / "nope.csv"
        result = run_replay(missing, "--summary")
        self.assert_input_error(result)

    def test_invalid_duplicate_policy_no_summary(self) -> None:
        path = self.write_csv([HEADER, *ACCEPTANCE_ROWS])
        result = run_replay(path, "--summary", "--duplicate-policy", "keep")
        self.assert_input_error(result)
        self.assertIn("--duplicate-policy", result.stderr)


class TestSummaryFlagShape(SummaryCliTestCase):
    """--summary 是无值开关：带值一律拒绝且错误点名该参数。"""

    def assert_summary_flag_rejected(self, *extra_args: str) -> None:
        path = self.write_csv([HEADER, *ACCEPTANCE_ROWS])
        result = run_replay(path, *extra_args)
        self.assert_input_error(result)
        self.assertIn("--summary", result.stderr)

    def test_equals_true_rejected(self) -> None:
        self.assert_summary_flag_rejected("--summary=true")

    def test_equals_empty_rejected(self) -> None:
        self.assert_summary_flag_rejected("--summary=")

    def test_equals_one_rejected(self) -> None:
        self.assert_summary_flag_rejected("--summary=1")

    def test_positional_value_rejected(self) -> None:
        # 开关后的游离取值不会被吞成开关的值：仍是退出码 2、空标准输出。
        path = self.write_csv([HEADER, *ACCEPTANCE_ROWS])
        result = run_replay(path, "--summary", "yes")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertNotIn("Traceback", result.stderr)

    def test_help_documents_flag(self) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "sensor_replay", "--help"],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("--summary", result.stdout)
        self.assertIn("sample_count", result.stdout)


if __name__ == "__main__":
    unittest.main()
