"""最终记录选择流程局部重构的兼容性回归测试。

本次重构把回放（render_records）与统计摘要（build_summary）原先各自维护
的“闭区间筛选 → 时间戳稳定排序 → 重复策略 → 最小间隔抽样”调用收敛到唯一
入口 finalize_records；本文件核对该重构不改变任何既有行为：

- 直接核对纯步骤 select_records / sample_min_interval 的签名与结果保留；
- 核对 finalize_records 恰好等于旧的两步组合（重构等价性）；
- 对多组数据与参数组合，核对回放输出与摘要统计来自**同一批**最终记录；
- 用仓库自带 check.csv 复刻验收命令（默认 CSV 入口，不传 --format）；
- 核对 replay_text 别名、文本入口 InputError、CSV 逻辑记录序号与 JSONL
  含空白行的物理行号定位均保持兼容。

回放/摘要的其余命令行行为由既有 CLI 测试覆盖，本文件不重复；这里的进程内
用例直接 import sensor_replay.__main__，CLI 用例仍走子进程公开入口。

在项目根目录执行::

    python3 -m unittest discover -s tests
"""

from __future__ import annotations

import inspect
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from sensor_replay import __main__ as sr  # noqa: E402

CHECK_CSV = PROJECT_ROOT / "check.csv"

# check.csv 的源顺序（乱序，1000 出现两次且温湿度不同；900/1900 会在
# --min-interval-ms 1000 下被抽样跳过）。
CHECK_ROWS = [
    (1900, 29, 69),
    (1000, 20, 60),
    (0, 10, 50),
    (900, 19, 59),
    (1000, 21, 61),
    (2000, 30, 70),
]

# --min-interval-ms 1000 下的最终记录（时间戳、温湿度、elapsed_ms）。
EXPECTED_FINAL = [
    (0, 10, 50, 0),
    (1000, 20, 60, 1000),
    (1000, 21, 61, 1000),
    (2000, 30, 70, 2000),
]
EXPECTED_SUMMARY = {
    "sample_count": 4,
    "first_ms": 0,
    "last_ms": 2000,
    "duration_ms": 2000,
    "temperature": {"min": 10, "max": 30},
    "humidity": {"min": 50, "max": 70},
}
EMPTY_SUMMARY = {
    "sample_count": 0,
    "first_ms": None,
    "last_ms": None,
    "duration_ms": 0,
    "temperature": {"min": None, "max": None},
    "humidity": {"min": None, "max": None},
}

Record = tuple[int, int | float, int | float]


def run_cli(*extra_args: str, data_format: str | None = None):
    """以子进程运行 ``python -m sensor_replay``；data_format 为 None 时省略。"""
    cmd = [sys.executable, "-m", "sensor_replay", str(CHECK_CSV)]
    if data_format is not None:
        cmd += ["--format", data_format]
    cmd += list(extra_args)
    return subprocess.run(
        cmd,
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def parse_jsonl(stdout: str) -> list[dict]:
    return [json.loads(line) for line in stdout.splitlines()]


class TestFinalizeComposition(unittest.TestCase):
    """finalize_records 必须恰好等价于旧的 select + sample 两步组合。"""

    def test_pure_stages_still_available_with_original_behaviour(self) -> None:
        # 重构后两个纯步骤仍可独立调用，签名与旧行为保留。
        selected = sr.select_records(list(CHECK_ROWS), None, None, "all")
        self.assertEqual(
            [r[0] for r in selected], [0, 900, 1000, 1000, 1900, 2000]
        )
        sampled = sr.sample_min_interval(selected, 1000)
        self.assertEqual(
            [(r[0], r[1], r[2]) for r in sampled],
            [(ts, t, h) for ts, t, h, _ in EXPECTED_FINAL],
        )
        # min_interval_ms 为 None 时抽样步骤原样返回。
        self.assertIs(
            sr.sample_min_interval(selected, None), selected
        )

    def test_finalize_equals_two_step_composition(self) -> None:
        cases = [
            (CHECK_ROWS, None, None, "all", 1000),
            (CHECK_ROWS, None, None, "first", 1000),
            (CHECK_ROWS, None, None, "last", 1000),
            (CHECK_ROWS, 900, 1900, "all", 1000),
            (CHECK_ROWS, 1000, 2000, "all", None),
            (CHECK_ROWS, None, None, "all", None),
            ([], None, None, "all", 1000),
        ]
        for rows, start, end, policy, interval in cases:
            with self.subTest(
                start=start, end=end, policy=policy, interval=interval
            ):
                composed = sr.sample_min_interval(
                    sr.select_records(list(rows), start, end, policy),
                    interval,
                )
                unified = sr.finalize_records(
                    list(rows), start, end, policy, interval
                )
                self.assertEqual(unified, composed)

    def test_pipeline_order_and_rules(self) -> None:
        # 闭区间先于抽样：区间 [900,2000] 内最早的 900 成为抽样首组。
        finalized = sr.finalize_records(
            list(CHECK_ROWS), 900, 2000, "all", 1000
        )
        self.assertEqual(
            [r[0] for r in finalized], [900, 1900]
        )
        # first/last 取源文件中最先/最后出现的完整记录，不拼接不平均。
        first = sr.finalize_records(
            list(CHECK_ROWS), None, None, "first", 1000
        )
        last = sr.finalize_records(
            list(CHECK_ROWS), None, None, "last", 1000
        )
        self.assertEqual(
            first, [(0, 10, 50), (1000, 20, 60), (2000, 30, 70)]
        )
        self.assertEqual(
            last, [(0, 10, 50), (1000, 21, 61), (2000, 30, 70)]
        )

    def test_empty_selection(self) -> None:
        self.assertEqual(
            sr.finalize_records(list(CHECK_ROWS), 9000, 9999, "all", 1000),
            []
        )


class TestBothOutputsShareFinalBatch(unittest.TestCase):
    """回放记录与摘要统计必须派生自同一批最终记录。"""

    DATASETS: list[list[Record]] = [
        CHECK_ROWS,
        [(2400, 4, 4), (900, 2, 2), (0, 1, 1), (1500, 3, 3)],
        [(100, 2, 2), (100, 1, 1), (100, 3, 3)],
        [(42, 7.5, 9)],
        [],
    ]
    PARAM_SETS = [
        (None, None, "all", None),
        (None, None, "all", 1000),
        (None, None, "first", 1000),
        (None, None, "last", 1000),
        (0, 2000, "all", 1000),
        (1000, 2000, "all", 700),
    ]

    def test_summary_matches_replay_final_records(self) -> None:
        for rows in self.DATASETS:
            for start, end, policy, interval in self.PARAM_SETS:
                with self.subTest(
                    n=len(rows), start=start, end=end,
                    policy=policy, interval=interval,
                ):
                    replay_text = sr.render_records(
                        list(rows), start, end, None, policy, interval
                    )
                    summary = sr.build_summary(
                        list(rows), start, end, policy, interval
                    )
                    replayed = parse_jsonl(replay_text)
                    self.assertEqual(
                        summary["sample_count"], len(replayed)
                    )
                    if not replayed:
                        self.assertEqual(summary, EMPTY_SUMMARY)
                        continue
                    timestamps = [r["timestamp_ms"] for r in replayed]
                    self.assertEqual(summary["first_ms"], timestamps[0])
                    self.assertEqual(summary["last_ms"], timestamps[-1])
                    self.assertEqual(
                        summary["duration_ms"],
                        timestamps[-1] - timestamps[0],
                    )
                    self.assertEqual(
                        summary["temperature"],
                        {
                            "min": min(r["temperature"] for r in replayed),
                            "max": max(r["temperature"] for r in replayed),
                        },
                    )
                    self.assertEqual(
                        summary["humidity"],
                        {
                            "min": min(r["humidity"] for r in replayed),
                            "max": max(r["humidity"] for r in replayed),
                        },
                    )

    def test_both_paths_call_single_finalize_entry(self) -> None:
        # 结构性核对：两条输出路径都只经过统一入口 finalize_records 一次，
        # 防止筛选/抽样组合被重新内联成两份。
        csv_text = (
            "timestamp_ms,temperature,humidity\n"
            + "\n".join(f"{ts},{t},{h}" for ts, t, h in CHECK_ROWS)
            + "\n"
        )
        jsonl_text = "\n".join(
            json.dumps(
                {"timestamp_ms": ts, "temperature": t, "humidity": h},
                separators=(",", ":"),
            )
            for ts, t, h in CHECK_ROWS
        )
        original = sr.finalize_records
        calls: list[str] = []

        def counting_finalize(records, start_ms, end_ms,
                              duplicate_policy="all", min_interval_ms=None):
            calls.append(duplicate_policy)
            return original(
                records, start_ms, end_ms,
                duplicate_policy, min_interval_ms,
            )

        sr.finalize_records = counting_finalize
        try:
            sr.replay_csv_text(csv_text, None, None, None, "all", False, 1000)
            sr.replay_csv_text(csv_text, None, None, None, "all", True, 1000)
            sr.replay_jsonl_text(
                jsonl_text, None, None, None, "first", False, 1000
            )
            sr.replay_jsonl_text(
                jsonl_text, None, None, None, "last", True, 1000
            )
        finally:
            sr.finalize_records = original
        self.assertEqual(calls, ["all", "all", "first", "last"])

    def test_gap_threshold_does_not_change_selection_or_summary(self) -> None:
        without = sr.render_records(
            list(CHECK_ROWS), None, None, None, "all", 1000
        )
        with_gap = sr.render_records(
            list(CHECK_ROWS), None, None, 700, "all", 1000
        )
        self.assertEqual(
            [(r["timestamp_ms"], r["temperature"], r["humidity"])
             for r in parse_jsonl(with_gap)],
            [(r["timestamp_ms"], r["temperature"], r["humidity"])
             for r in parse_jsonl(without)],
        )
        # missing_before 仍比较最终相邻记录：1000-0 严格大于 700 为 true。
        self.assertEqual(
            [r["missing_before"] for r in parse_jsonl(with_gap)],
            [False, True, False, True],
        )
        summary_with_gap = sr.render_summary(
            list(CHECK_ROWS), None, None, "all", 1000
        )
        self.assertEqual(json.loads(summary_with_gap), EXPECTED_SUMMARY)


class TestPublicSignaturesAndAlias(unittest.TestCase):
    """命令行与文本回放入口签名、replay_text 别名保持兼容。"""

    def test_text_entry_signatures_unchanged(self) -> None:
        expected = (
            "text", "start_ms", "end_ms", "gap_threshold_ms",
            "duplicate_policy", "summary", "min_interval_ms",
        )
        for function in (sr.replay_csv_text, sr.replay_jsonl_text):
            self.assertEqual(
                tuple(inspect.signature(function).parameters), expected
            )

    def test_replay_text_is_csv_alias(self) -> None:
        self.assertIs(sr.replay_text, sr.replay_csv_text)
        text = (
            "timestamp_ms,temperature,humidity\n"
            + "\n".join(f"{ts},{t},{h}" for ts, t, h in CHECK_ROWS)
            + "\n"
        )
        self.assertEqual(
            sr.replay_text(text, None, None, None, "all", False, 1000),
            sr.replay_csv_text(text, None, None, None, "all", False, 1000),
        )


class TestCheckCsvAcceptanceCli(unittest.TestCase):
    """复刻用户的 check.csv 验收命令（默认 CSV，省略 --format）。"""

    def test_replay_command_exact_output(self) -> None:
        result = run_cli("--min-interval-ms", "1000")
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(result.stderr, "")
        records = parse_jsonl(result.stdout)
        self.assertEqual(
            [(r["timestamp_ms"], r["temperature"], r["humidity"],
              r["elapsed_ms"]) for r in records],
            EXPECTED_FINAL,
        )
        for record in records:
            self.assertEqual(
                set(record.keys()),
                {"timestamp_ms", "elapsed_ms", "temperature", "humidity"},
            )
        # 每行一个 JSON 对象，整体恰好四行并以单个换行结尾。
        self.assertTrue(result.stdout.endswith("\n"))
        self.assertEqual(len(result.stdout.splitlines()), 4)

    def test_summary_command_single_json_object(self) -> None:
        result = run_cli("--summary", "--min-interval-ms", "1000")
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(result.stderr, "")
        # 只输出一个 JSON 对象及换行（无第二个 JSON 行、无多余空白）。
        self.assertTrue(result.stdout.endswith("\n"))
        body = result.stdout[:-1]
        self.assertNotIn("\n", body)
        summary = json.loads(body)
        self.assertEqual(set(summary.keys()), set(EXPECTED_SUMMARY))
        self.assertEqual(summary, EXPECTED_SUMMARY)

    def test_jsonl_explicit_format_matches(self) -> None:
        # 与 check.csv 等价的 JSONL 显式格式结果相同（摘要逐字节一致）。
        with tempfile.TemporaryDirectory() as tmp:
            jsonl_path = Path(tmp) / "samples.jsonl"
            jsonl_path.write_text(
                "\n".join(
                    json.dumps(
                        {"timestamp_ms": ts, "temperature": t,
                         "humidity": h},
                        separators=(",", ":"),
                    )
                    for ts, t, h in CHECK_ROWS
                )
                + "\n",
                encoding="utf-8",
            )
            csv_result = run_cli("--summary", "--min-interval-ms", "1000")
            jsonl_result = subprocess.run(
                [
                    sys.executable, "-m", "sensor_replay",
                    str(jsonl_path), "--format", "jsonl",
                    "--summary", "--min-interval-ms", "1000",
                ],
                cwd=PROJECT_ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            self.assertEqual(
                jsonl_result.returncode, 0, msg=jsonl_result.stderr
            )
            self.assertEqual(jsonl_result.stderr, "")
            self.assertEqual(jsonl_result.stdout, csv_result.stdout)


class TestInvalidRecordsStillFailCompat(unittest.TestCase):
    """区间外或会被丢弃的非法记录仍使整次处理失败，定位约定不变。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp_dir = Path(self._tmp.name)

    def test_text_entry_raises_input_error_with_logical_record(self) -> None:
        # 50 组会被抽样舍弃，但其 NaN 温度仍先在整文件校验阶段失败；
        # CSV 定位按逻辑记录序号（表头第 1 条，故该行为第 3 条）。
        text = (
            "timestamp_ms,temperature,humidity\n"
            "0,1,1\n"
            "50,NaN,2\n"
            "2000,3,3\n"
        )
        for entry in (sr.replay_csv_text, sr.replay_text):
            with self.subTest(entry=entry.__name__):
                with self.assertRaises(sr.InputError) as ctx:
                    entry(text, None, None, None, "all", False, 1000)
                self.assertEqual(ctx.exception.record, 3)
                self.assertIsNone(ctx.exception.line)

    def test_cli_outside_interval_invalid_record(self) -> None:
        # 9000 落在区间外且会被抽样舍弃，仍按第 4 条 CSV 记录报错。
        path = self.tmp_dir / "bad.csv"
        path.write_text(
            "timestamp_ms,temperature,humidity\n"
            "0,1,1\n1000,2,2\n9000,NaN,3\n",
            encoding="utf-8",
        )
        result = subprocess.run(
            [
                sys.executable, "-m", "sensor_replay", str(path),
                "--min-interval-ms", "1000",
                "--start-ms", "0", "--end-ms", "2000",
            ],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn("第 4 条 CSV 记录", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_cli_jsonl_physical_line_counts_blank_line(self) -> None:
        # 第 1 行合法、第 2 行为空白行（计数但跳过）、第 3 行非法。
        path = self.tmp_dir / "bad.jsonl"
        path.write_text(
            '{"timestamp_ms":0,"temperature":1,"humidity":1}\n'
            "\n"
            '{"timestamp_ms":50,"temperature":"x","humidity":2}\n',
            encoding="utf-8",
        )
        result = subprocess.run(
            [
                sys.executable, "-m", "sensor_replay", str(path),
                "--format", "jsonl", "--min-interval-ms", "1000",
            ],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn("第 3 行", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_summary_invalid_record_still_fails(self) -> None:
        text = (
            "timestamp_ms,temperature,humidity\n"
            "0,1,1\n"
            "50,NaN,2\n"
            "2000,3,3\n"
        )
        with self.assertRaises(sr.InputError) as ctx:
            sr.replay_csv_text(text, None, None, None, "all", True, 1000)
        self.assertEqual(ctx.exception.record, 3)


if __name__ == "__main__":
    unittest.main()
