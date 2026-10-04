"""``--min-interval-ms`` 最小时间间隔抽样的命令行回归测试。

测试自备临时 UTF-8 CSV / JSONL 文件，通过子进程调用公开入口
``python -m sensor_replay``，核对退出码、标准输出与标准错误。抽样在整文件
校验通过后，以闭区间筛选与重复点策略确定的记录为范围，按时间戳组进行：
最早组始终保留；其后仅当与上一个**保留**组的差值至少为 MS（恰好相等也
保留）时才整体保留；被跳过的组不移动基准；末组不足间隔不强行保留。

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

# 验收数据（源顺序乱序，1000 出现两次且温湿度不同）。
ACCEPTANCE_ROWS = [
    "1900,29,69",
    "1000,20,60",
    "0,10,50",
    "700,17,57",
    "1000,21,61",
    "2000,30,70",
]
ACCEPTANCE_JSONL = [
    {"timestamp_ms": 1900, "temperature": 29, "humidity": 69},
    {"timestamp_ms": 1000, "temperature": 20, "humidity": 60},
    {"timestamp_ms": 0, "temperature": 10, "humidity": 50},
    {"timestamp_ms": 700, "temperature": 17, "humidity": 57},
    {"timestamp_ms": 1000, "temperature": 21, "humidity": 61},
    {"timestamp_ms": 2000, "temperature": 30, "humidity": 70},
]

EXPECTED_KEYS = {"timestamp_ms", "elapsed_ms", "temperature", "humidity"}
GAP_KEYS = EXPECTED_KEYS | {"missing_before"}

EMPTY_SUMMARY = {
    "sample_count": 0,
    "first_ms": None,
    "last_ms": None,
    "duration_ms": 0,
    "temperature": {"min": None, "max": None},
    "humidity": {"min": None, "max": None},
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


class MinIntervalTestCase(unittest.TestCase):
    """公共基类：在临时目录中准备 CSV / JSONL 文件。"""

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

    def run_jsonl(
        self, path: Path, *extra_args: str
    ) -> subprocess.CompletedProcess:
        return run_replay(path, *extra_args, data_format="jsonl")

    @staticmethod
    def parse_records(stdout: str) -> list[dict]:
        return [json.loads(line) for line in stdout.splitlines()]

    def assert_success(
        self,
        result: subprocess.CompletedProcess,
        expected_keys: set[str] = EXPECTED_KEYS,
    ) -> list[dict]:
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(result.stderr, "")
        records = self.parse_records(result.stdout)
        for record in records:
            self.assertEqual(set(record.keys()), expected_keys)
        return records

    def assert_input_error(self, result: subprocess.CompletedProcess) -> None:
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertNotEqual(result.stderr, "")
        self.assertNotIn("Traceback", result.stderr)


class TestMinIntervalAcceptance(MinIntervalTestCase):
    """验收场景：--min-interval-ms 1000 的 CSV 与 JSONL。"""

    def test_csv_acceptance(self) -> None:
        path = self.write_csv([HEADER, *ACCEPTANCE_ROWS])
        result = run_replay(path, INTERVAL_FLAG, "1000")
        records = self.assert_success(result)
        self.assertEqual(
            [r["timestamp_ms"] for r in records], [0, 1000, 1000, 2000]
        )
        self.assertEqual(
            [(r["temperature"], r["humidity"]) for r in records],
            [(10, 50), (20, 60), (21, 61), (30, 70)],
        )
        self.assertEqual(
            [r["elapsed_ms"] for r in records], [0, 1000, 1000, 2000]
        )

    def test_jsonl_acceptance_identical(self) -> None:
        path = self.write_jsonl_objects(ACCEPTANCE_JSONL)
        result = self.run_jsonl(path, INTERVAL_FLAG, "1000")
        records = self.assert_success(result)
        self.assertEqual(
            [r["timestamp_ms"] for r in records], [0, 1000, 1000, 2000]
        )
        self.assertEqual(
            [(r["temperature"], r["humidity"]) for r in records],
            [(10, 50), (20, 60), (21, 61), (30, 70)],
        )
        self.assertEqual(
            [r["elapsed_ms"] for r in records], [0, 1000, 1000, 2000]
        )


class TestSamplingRules(MinIntervalTestCase):
    """抽样规则：组原子性、基准不移动、等号保留、末组不强行保留。"""

    def test_duplicate_group_kept_or_skipped_atomically(self) -> None:
        # 1000 组两条整体保留；700/1900 组被跳过。
        path = self.write_csv([HEADER, *ACCEPTANCE_ROWS])
        records = self.assert_success(
            run_replay(path, INTERVAL_FLAG, "1000")
        )
        kept = [r["timestamp_ms"] for r in records]
        self.assertEqual(kept.count(1000), 2)
        self.assertNotIn(700, kept)
        self.assertNotIn(1900, kept)

    def test_skipped_group_does_not_move_anchor(self) -> None:
        # 0 保留；900 差值不足被跳过且不移动基准；1500 与基准 0 相差
        # 1500 >= 1000 仍保留；2400 与新基准 1500 相差 900 被跳过，
        # 且末组不足间隔不强行保留。
        path = self.write_csv(
            [HEADER, "2400,4,4", "900,2,2", "0,1,1", "1500,3,3"]
        )
        records = self.assert_success(
            run_replay(path, INTERVAL_FLAG, "1000")
        )
        self.assertEqual(
            [(r["timestamp_ms"], r["temperature"]) for r in records],
            [(0, 1), (1500, 3)],
        )

    def test_difference_equal_to_threshold_is_kept(self) -> None:
        path = self.write_csv([HEADER, "0,1,1", "1500,2,2"])
        records = self.assert_success(
            run_replay(path, INTERVAL_FLAG, "1500")
        )
        self.assertEqual(
            [r["timestamp_ms"] for r in records], [0, 1500]
        )

    def test_last_group_not_forced_when_below_interval(self) -> None:
        # 0 保留、1000 保留；1900 与 1000 相差 900，作为末组不强行保留。
        path = self.write_csv([HEADER, "1900,3,3", "0,1,1", "1000,2,2"])
        records = self.assert_success(
            run_replay(path, INTERVAL_FLAG, "1000")
        )
        self.assertEqual(
            [r["timestamp_ms"] for r in records], [0, 1000]
        )

    def test_leading_zeros_accepted(self) -> None:
        path = self.write_csv([HEADER, *ACCEPTANCE_ROWS])
        records = self.assert_success(
            run_replay(path, INTERVAL_FLAG, "01000")
        )
        self.assertEqual(
            [r["timestamp_ms"] for r in records], [0, 1000, 1000, 2000]
        )

    def test_single_record_always_kept(self) -> None:
        path = self.write_csv([HEADER, "42,7,9"])
        for value in ("1", "1000"):
            records = self.assert_success(
                run_replay(path, INTERVAL_FLAG, value)
            )
            self.assertEqual([r["timestamp_ms"] for r in records], [42])
            self.assertEqual([r["elapsed_ms"] for r in records], [0])

    def test_all_equal_timestamps_kept_in_source_order(self) -> None:
        path = self.write_csv(
            [HEADER, "100,2,2", "100,1,1", "100,3,3"]
        )
        records = self.assert_success(
            run_replay(path, INTERVAL_FLAG, "1000")
        )
        # 同一时间戳整体保留（首组），维持源文件先后顺序。
        self.assertEqual(
            [r["temperature"] for r in records], [2, 1, 3]
        )

    def test_omitted_flag_keeps_all_records(self) -> None:
        path = self.write_csv([HEADER, *ACCEPTANCE_ROWS])
        without = run_replay(path)
        with_flag = run_replay(path, INTERVAL_FLAG, "1")
        # MS=1 时相邻组差值均 >= 1（重复组差值 0 随首组整体保留），
        # 与省略参数的输出完全一致。
        self.assertEqual(with_flag.returncode, 0)
        self.assertEqual(with_flag.stdout, without.stdout)


class TestSamplingWithDuplicatePolicy(MinIntervalTestCase):
    """抽样与 first/last 组合：每组只含策略保留的完整记录。"""

    def test_first(self) -> None:
        path = self.write_csv([HEADER, *ACCEPTANCE_ROWS])
        records = self.assert_success(
            run_replay(
                path, INTERVAL_FLAG, "1000", "--duplicate-policy", "first"
            )
        )
        self.assertEqual(
            [r["timestamp_ms"] for r in records], [0, 1000, 2000]
        )
        self.assertEqual(
            [(r["temperature"], r["humidity"]) for r in records],
            [(10, 50), (20, 60), (30, 70)],
        )

    def test_last(self) -> None:
        path = self.write_csv([HEADER, *ACCEPTANCE_ROWS])
        records = self.assert_success(
            run_replay(
                path, INTERVAL_FLAG, "1000", "--duplicate-policy", "last"
            )
        )
        self.assertEqual(
            [r["timestamp_ms"] for r in records], [0, 1000, 2000]
        )
        # 1000 组取源文件中最后出现的 21/61。
        self.assertEqual(
            [(r["temperature"], r["humidity"]) for r in records],
            [(10, 50), (21, 61), (30, 70)],
        )


class TestSamplingWithIntervalAndGap(MinIntervalTestCase):
    """抽样与区间、elapsed_ms 重基、缺测标记组合。"""

    def test_elapsed_rebased_after_interval_and_sampling(self) -> None:
        # 区间 [1000,2000] 选中 1000,1000,2000；MS 1000 全部保留；
        # elapsed_ms 从抽样后首条 1000 计起。
        path = self.write_csv([HEADER, *ACCEPTANCE_ROWS])
        records = self.assert_success(
            run_replay(
                path,
                "--start-ms",
                "1000",
                "--end-ms",
                "2000",
                INTERVAL_FLAG,
                "1000",
            )
        )
        self.assertEqual(
            [r["timestamp_ms"] for r in records], [1000, 1000, 2000]
        )
        self.assertEqual(
            [r["elapsed_ms"] for r in records], [0, 0, 1000]
        )

    def test_gap_compares_sampled_adjacent_records(self) -> None:
        # 抽样后 0,1000,1000,2000；阈值 700：首条 false；1000-0=1000
        # 严格大于 700 为 true；重复组差值 0 为 false；2000-1000=1000
        # 严格大于 700 为 true。被抽样舍弃的 700/1900 不参与比较。
        path = self.write_csv([HEADER, *ACCEPTANCE_ROWS])
        records = self.assert_success(
            run_replay(
                path,
                INTERVAL_FLAG,
                "1000",
                "--gap-threshold-ms",
                "700",
            ),
            GAP_KEYS,
        )
        self.assertEqual(
            [r["timestamp_ms"] for r in records], [0, 1000, 1000, 2000]
        )
        self.assertEqual(
            [r["missing_before"] for r in records],
            [False, True, False, True],
        )

    def test_gap_equality_after_sampling_is_false(self) -> None:
        # 0 与 1500 均保留，差值恰好等于阈值：false。
        path = self.write_csv(
            [HEADER, "0,1,1", "900,2,2", "1500,3,3", "2400,4,4"]
        )
        records = self.assert_success(
            run_replay(
                path,
                INTERVAL_FLAG,
                "1000",
                "--gap-threshold-ms",
                "1500",
            ),
            GAP_KEYS,
        )
        self.assertEqual(
            [r["timestamp_ms"] for r in records], [0, 1500]
        )
        self.assertEqual(
            [r["missing_before"] for r in records], [False, False]
        )


class TestMinIntervalEmptySelection(MinIntervalTestCase):
    """抽样后无记录：回放为空；摘要为既有空摘要。"""

    def test_interval_without_hits_silent(self) -> None:
        path = self.write_csv([HEADER, *ACCEPTANCE_ROWS])
        result = run_replay(
            path, INTERVAL_FLAG, "1000", "--start-ms", "9000"
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "")

    def test_header_only_silent(self) -> None:
        path = self.write_csv([HEADER])
        result = run_replay(path, INTERVAL_FLAG, "1000")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "")

    def test_empty_jsonl_silent(self) -> None:
        path = self.write_jsonl_objects([])
        result = self.run_jsonl(path, INTERVAL_FLAG, "1000")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "")

    def test_summary_empty_selection(self) -> None:
        path = self.write_csv([HEADER, *ACCEPTANCE_ROWS])
        result = run_replay(
            path, "--summary", INTERVAL_FLAG, "1000", "--start-ms", "9000"
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertEqual(json.loads(result.stdout), EMPTY_SUMMARY)

    def test_summary_header_only(self) -> None:
        path = self.write_csv([HEADER])
        result = run_replay(path, "--summary", INTERVAL_FLAG, "1000")
        self.assertEqual(json.loads(result.stdout), EMPTY_SUMMARY)


class TestMinIntervalSummary(MinIntervalTestCase):
    """--summary 统计抽样后的最终记录。"""

    def test_summary_counts_sampled_records(self) -> None:
        path = self.write_csv([HEADER, *ACCEPTANCE_ROWS])
        result = run_replay(path, "--summary", INTERVAL_FLAG, "1000")
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(
            json.loads(result.stdout),
            {
                "sample_count": 4,
                "first_ms": 0,
                "last_ms": 2000,
                "duration_ms": 2000,
                "temperature": {"min": 10, "max": 30},
                "humidity": {"min": 50, "max": 70},
            },
        )

    def test_summary_with_first_policy(self) -> None:
        path = self.write_csv([HEADER, *ACCEPTANCE_ROWS])
        result = run_replay(
            path,
            "--summary",
            INTERVAL_FLAG,
            "1000",
            "--duplicate-policy",
            "first",
        )
        summary = json.loads(result.stdout)
        self.assertEqual(summary["sample_count"], 3)
        self.assertEqual(summary["first_ms"], 0)
        self.assertEqual(summary["last_ms"], 2000)
        self.assertEqual(summary["temperature"], {"min": 10, "max": 30})

    def test_summary_jsonl_matches_csv(self) -> None:
        csv_path = self.write_csv([HEADER, *ACCEPTANCE_ROWS])
        jsonl_path = self.write_jsonl_objects(ACCEPTANCE_JSONL)
        csv_result = run_replay(
            csv_path, "--summary", INTERVAL_FLAG, "1000"
        )
        jsonl_result = self.run_jsonl(
            jsonl_path, "--summary", INTERVAL_FLAG, "1000"
        )
        self.assertEqual(jsonl_result.stdout, csv_result.stdout)


class TestInvalidMinInterval(MinIntervalTestCase):
    """非法取值：退出码 2、空标准输出、错误点名 --min-interval-ms、无堆栈。"""

    def assert_interval_rejected(self, value: str | None) -> None:
        path = self.write_csv([HEADER, *ACCEPTANCE_ROWS])
        args = [INTERVAL_FLAG] if value is None else [INTERVAL_FLAG, value]
        result = run_replay(path, *args)
        self.assert_input_error(result)
        self.assertIn("--min-interval-ms", result.stderr)

    def test_missing_value(self) -> None:
        self.assert_interval_rejected(None)

    def test_empty_string(self) -> None:
        self.assert_interval_rejected("")

    def test_zero_rejected(self) -> None:
        for value in ("0", "00", "0000"):
            self.assert_interval_rejected(value)

    def test_signed(self) -> None:
        self.assert_interval_rejected("+1")
        self.assert_interval_rejected("-1")

    def test_decimal_and_exponent(self) -> None:
        for value in ("1.0", "1.5", "1e3", "2E3"):
            self.assert_interval_rejected(value)

    def test_whitespace_and_other_non_digits(self) -> None:
        for value in (
            " 1",
            "1 ",
            "1 0",
            "abc",
            "0x1",
            "1_0",
            "１２３",
        ):
            self.assert_interval_rejected(value)

    def test_equals_form_zero_rejected(self) -> None:
        path = self.write_csv([HEADER, *ACCEPTANCE_ROWS])
        result = run_replay(path, "--min-interval-ms=0")
        self.assert_input_error(result)
        self.assertIn("--min-interval-ms", result.stderr)

    def test_rejected_even_when_no_records_would_be_read(self) -> None:
        # 参数校验先于文件解析：即使文件不存在，非法取值仍按参数错误拒绝。
        missing = self.tmp_dir / "nope.csv"
        result = run_replay(missing, INTERVAL_FLAG, "0")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn("--min-interval-ms", result.stderr)


class TestInvalidRecordsStillFail(MinIntervalTestCase):
    """区间外或会被重复策略、抽样舍弃的非法记录仍使整个输入失败。"""

    def test_sampled_away_invalid_record_fails_csv(self) -> None:
        # 50 组会因 MS 1000 被抽样舍弃，但其中的 NaN 仍使整次失败，
        # 定位保留原有 CSV 记录序号（第 3 条）。
        path = self.write_csv(
            [HEADER, "0,1,1", "50,NaN,2", "2000,3,3"]
        )
        result = run_replay(path, INTERVAL_FLAG, "1000")
        self.assert_input_error(result)
        self.assertIn("第 3 条 CSV 记录", result.stderr)
        self.assertIn("temperature", result.stderr)

    def test_outside_interval_invalid_record_fails_csv(self) -> None:
        # 9000 落在区间外且会被抽样舍弃，仍按原定位报错（第 4 条）。
        path = self.write_csv(
            [HEADER, "0,1,1", "1000,2,2", "9000,NaN,3"]
        )
        result = run_replay(
            path,
            INTERVAL_FLAG,
            "1000",
            "--start-ms",
            "0",
            "--end-ms",
            "2000",
        )
        self.assert_input_error(result)
        self.assertIn("第 4 条 CSV 记录", result.stderr)

    def test_dropped_by_policy_invalid_record_fails_csv(self) -> None:
        # first 会舍弃后出现的 1000 非法记录，整文件校验仍先失败。
        path = self.write_csv(
            [
                HEADER,
                "1000,20,60",
                "0,10,50",
                "1000,NaN,61",
                "2000,30,70",
            ]
        )
        result = run_replay(
            path,
            INTERVAL_FLAG,
            "1000",
            "--duplicate-policy",
            "first",
        )
        self.assert_input_error(result)
        self.assertIn("第 4 条 CSV 记录", result.stderr)

    def test_sampled_away_invalid_record_fails_jsonl(self) -> None:
        path = self.tmp_dir / "samples.jsonl"
        path.write_text(
            "\n".join(
                [
                    '{"timestamp_ms":0,"temperature":1,"humidity":1}',
                    '{"timestamp_ms":50,"temperature":"x","humidity":2}',
                    '{"timestamp_ms":2000,"temperature":3,"humidity":3}',
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        result = self.run_jsonl(path, INTERVAL_FLAG, "1000")
        self.assert_input_error(result)
        self.assertIn("第 2 行", result.stderr)

    def test_invalid_record_with_summary_still_fails(self) -> None:
        path = self.write_csv(
            [HEADER, "0,1,1", "50,NaN,2", "2000,3,3"]
        )
        result = run_replay(
            path, "--summary", INTERVAL_FLAG, "1000"
        )
        self.assert_input_error(result)
        self.assertIn("第 3 条 CSV 记录", result.stderr)


if __name__ == "__main__":
    unittest.main()
