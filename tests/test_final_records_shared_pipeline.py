"""最终记录选择流程局部重构的回归测试。

回放与统计摘要共用同一处维护的选择流程：整文件先校验，再依次执行闭区间
筛选、timestamp_ms 稳定排序、重复策略（all/first/last）与最小间隔抽样，
回放（elapsed_ms、missing_before）与摘要（计数、跨度、极值）都基于同一批
最终记录。本测试核对：

1. 交付夹具 ``check.csv`` 的命令行验收输出：
   ``python -m sensor_replay check.csv --min-interval-ms 1000`` 与追加
   ``--summary`` 两条命令的退出码、标准输出与标准错误（逐字节精确比对）；
2. 共用流程 ``final_records`` 各阶段语义，以及回放/摘要确实经由同一函数；
3. 兼容性：旧的 ``replay_text`` 别名、``select_records`` 与
   ``sample_min_interval`` 仍可单独调用且组合结果与新流程一致；
4. 区间外或会被抽样、重复策略丢弃的非法记录仍使整次处理失败：文本入口
   抛出 ``InputError``，命令行退出 2、标准输出为空、标准错误保留定位且无
   堆栈；CSV 定位用逻辑记录序号，JSONL 定位用含空白行的物理行号。

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
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from sensor_replay import __main__ as sr  # noqa: E402

CHECK_CSV = PROJECT_ROOT / "check.csv"

# check.csv 的约定内容：表头后六条样本，乱序，1000 重复且温湿度不同。
CHECK_CSV_TEXT = (
    "timestamp_ms,temperature,humidity\n"
    "1900,29,69\n"
    "1000,20,60\n"
    "0,10,50\n"
    "900,19,59\n"
    "1000,21,61\n"
    "2000,30,70\n"
)

# --min-interval-ms 1000 的期望回放输出（逐字节）：900 组差值不足被跳过且
# 不移动基准；1000 组与基准 0 差值恰好 1000，整组保留并维持源先后顺序。
EXPECTED_REPLAY = (
    '{"timestamp_ms":0,"elapsed_ms":0,"temperature":10,"humidity":50}\n'
    '{"timestamp_ms":1000,"elapsed_ms":1000,"temperature":20,"humidity":60}\n'
    '{"timestamp_ms":1000,"elapsed_ms":1000,"temperature":21,"humidity":61}\n'
    '{"timestamp_ms":2000,"elapsed_ms":2000,"temperature":30,"humidity":70}\n'
)

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

# 与 check.csv 同源顺序对应的解析后记录。
CHECK_RECORDS = [
    (1900, 29, 69),
    (1000, 20, 60),
    (0, 10, 50),
    (900, 19, 59),
    (1000, 21, 61),
    (2000, 30, 70),
]
EXPECTED_FINAL = [
    (0, 10, 50),
    (1000, 20, 60),
    (1000, 21, 61),
    (2000, 30, 70),
]


def run_cli(*extra_args: str) -> subprocess.CompletedProcess:
    """在项目根目录以子进程运行公开入口，捕获两个输出流。"""
    return subprocess.run(
        [sys.executable, "-m", "sensor_replay", *extra_args],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


class CheckCsvFixtureTest(unittest.TestCase):
    """交付夹具 check.csv 及其两条验收命令。"""

    def test_fixture_content_exact(self) -> None:
        self.assertTrue(CHECK_CSV.is_file(), msg="check.csv 缺失")
        self.assertEqual(CHECK_CSV.read_text(encoding="utf-8"), CHECK_CSV_TEXT)

    def test_replay_min_interval_exact_output(self) -> None:
        result = run_cli("check.csv", "--min-interval-ms", "1000")
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(result.stdout, EXPECTED_REPLAY)

    def test_summary_min_interval_single_object(self) -> None:
        result = run_cli(
            "check.csv", "--min-interval-ms", "1000", "--summary"
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(result.stderr, "")
        # 恰好一个 JSON 对象加末尾换行：没有逐行输出、没有多余空行。
        self.assertTrue(result.stdout.endswith("\n"))
        self.assertEqual(result.stdout.count("\n"), 1)
        self.assertEqual(len(result.stdout.splitlines()), 1)
        self.assertEqual(json.loads(result.stdout), EXPECTED_SUMMARY)


class FinalRecordsPipelineTest(unittest.TestCase):
    """final_records：闭区间筛选 → 稳定排序 → 重复策略 → 最小间隔抽样。"""

    def test_check_records_selection(self) -> None:
        self.assertEqual(
            sr.final_records(CHECK_RECORDS, None, None, "all", 1000),
            EXPECTED_FINAL,
        )

    def test_closed_interval_filter_before_sort(self) -> None:
        # 两端都包含：900 与 1900 均保留，区间外的 0 与 2000 被排除。
        self.assertEqual(
            sr.final_records(CHECK_RECORDS, 900, 1900, "all", None),
            [(900, 19, 59), (1000, 20, 60), (1000, 21, 61), (1900, 29, 69)],
        )

    def test_stable_sort_preserves_source_order_for_duplicates(self) -> None:
        selected = sr.final_records(CHECK_RECORDS, None, None, "all", None)
        dup_ts_1000 = [r for r in selected if r[0] == 1000]
        self.assertEqual(dup_ts_1000, [(1000, 20, 60), (1000, 21, 61)])

    def test_first_keeps_earliest_complete_record(self) -> None:
        self.assertEqual(
            sr.final_records(CHECK_RECORDS, 1000, 1000, "first", None),
            [(1000, 20, 60)],
        )

    def test_last_keeps_latest_complete_record(self) -> None:
        self.assertEqual(
            sr.final_records(CHECK_RECORDS, 1000, 1000, "last", None),
            [(1000, 21, 61)],
        )

    def test_sampling_equality_keeps_group(self) -> None:
        # 差值恰好等于阈值也保留。
        self.assertEqual(
            sr.final_records([(0, 1, 1), (1000, 2, 2)], None, None, "all", 1000),
            [(0, 1, 1), (1000, 2, 2)],
        )

    def test_skipped_group_does_not_move_baseline(self) -> None:
        # 跳过 900 后基准仍是 0；1500 与 0 的差值 1500 ≥ 1000，仍保留。
        self.assertEqual(
            sr.final_records(
                [(0, 1, 1), (900, 2, 2), (1500, 3, 3)],
                None,
                None,
                "all",
                1000,
            ),
            [(0, 1, 1), (1500, 3, 3)],
        )

    def test_last_short_group_is_not_force_kept(self) -> None:
        # 末组 1500 与上一个保留组 1000 差值不足，不补留。
        self.assertEqual(
            sr.final_records(
                [(0, 1, 1), (1000, 2, 2), (1500, 3, 3)],
                None,
                None,
                "all",
                1000,
            ),
            [(0, 1, 1), (1000, 2, 2)],
        )

    def test_group_kept_or_skipped_as_whole(self) -> None:
        # all 下同一时间戳两条整组保留；被跳过的组同样整组消失。
        records = [(0, 1, 1), (500, 2, 2), (500, 3, 3), (1000, 4, 4)]
        self.assertEqual(
            sr.final_records(records, None, None, "all", 1000),
            [(0, 1, 1), (1000, 4, 4)],
        )

    def test_no_interval_returns_all_sorted(self) -> None:
        self.assertEqual(
            sr.final_records(CHECK_RECORDS, None, None, "all", None),
            [
                (0, 10, 50),
                (900, 19, 59),
                (1000, 20, 60),
                (1000, 21, 61),
                (1900, 29, 69),
                (2000, 30, 70),
            ],
        )

    def test_empty_records(self) -> None:
        self.assertEqual(sr.final_records([], None, None, "all", 1000), [])


class SharedOutputConsistencyTest(unittest.TestCase):
    """回放与摘要必须取自同一批最终记录。"""

    def test_both_outputs_route_through_final_records(self) -> None:
        records = [(0, 10, 50), (500, 99, 99), (1000, 20, 60)]
        with patch.object(
            sr, "final_records", wraps=sr.final_records
        ) as spy:
            replay = sr.render_records(
                records, None, None, None, "all", 1000
            )
            summary = sr.build_summary(
                records, None, None, "all", 1000
            )
        self.assertEqual(spy.call_count, 2)

        # 500 组被抽样丢弃：回放与摘要都不得包含 99 这一极值。
        replayed = [json.loads(line) for line in replay.splitlines()]
        self.assertEqual([row["timestamp_ms"] for row in replayed], [0, 1000])
        self.assertEqual(
            [row["temperature"] for row in replayed], [10, 20]
        )
        self.assertEqual(summary["sample_count"], 2)
        self.assertEqual(summary["first_ms"], 0)
        self.assertEqual(summary["last_ms"], 1000)
        self.assertEqual(summary["duration_ms"], 1000)
        self.assertEqual(summary["temperature"], {"min": 10, "max": 20})
        self.assertEqual(summary["humidity"], {"min": 50, "max": 60})

    def test_elapsed_rebased_to_final_first_record(self) -> None:
        # 区间从 1000 开始：elapsed_ms 零点是首条选中记录而非参数边界。
        out = sr.render_records(
            [(0, 1, 1), (1000, 2, 2), (2500, 3, 3)],
            1000,
            None,
            None,
            "all",
            None,
        )
        rows = [json.loads(line) for line in out.splitlines()]
        self.assertEqual(
            [(r["timestamp_ms"], r["elapsed_ms"]) for r in rows],
            [(1000, 0), (2500, 1500)],
        )

    def test_missing_before_compares_final_neighbors(self) -> None:
        # 抽样后 0 与 2000 相邻，差值 2000 严格大于 1000；首条固定 false。
        out = sr.render_records(
            [(0, 10, 50), (900, 19, 59), (2000, 30, 70)],
            None,
            None,
            1000,
            "all",
            1000,
        )
        flags = [json.loads(line)["missing_before"] for line in out.splitlines()]
        self.assertEqual(flags, [False, True])

    def test_missing_before_equality_is_false(self) -> None:
        out = sr.render_records(
            [(0, 1, 1), (1000, 2, 2)],
            None,
            None,
            1000,
            "all",
            None,
        )
        flags = [json.loads(line)["missing_before"] for line in out.splitlines()]
        self.assertEqual(flags, [False, False])

    def test_empty_replay_and_summary(self) -> None:
        self.assertEqual(
            sr.render_records([], None, None, None, "all", 1000), ""
        )
        self.assertEqual(
            sr.build_summary([], None, None, "all", 1000), EMPTY_SUMMARY
        )
        rendered = sr.render_summary([], None, None, "all", 1000)
        self.assertEqual(json.loads(rendered), EMPTY_SUMMARY)
        self.assertTrue(rendered.endswith("\n"))


class CompatibilityContractTest(unittest.TestCase):
    """旧入口与分步函数保持兼容。"""

    def test_replay_text_alias(self) -> None:
        self.assertIs(sr.replay_text, sr.replay_csv_text)

    def test_stepwise_helpers_match_final_records(self) -> None:
        for kwargs in (
            dict(start_ms=None, end_ms=None, policy="all", interval=None),
            dict(start_ms=900, end_ms=2000, policy="first", interval=1000),
            dict(start_ms=0, end_ms=2000, policy="last", interval=500),
        ):
            with self.subTest(kwargs=kwargs):
                stepwise = sr.sample_min_interval(
                    sr.select_records(
                        list(CHECK_RECORDS),
                        kwargs["start_ms"],
                        kwargs["end_ms"],
                        kwargs["policy"],
                    ),
                    kwargs["interval"],
                )
                self.assertEqual(
                    stepwise,
                    sr.final_records(
                        list(CHECK_RECORDS),
                        kwargs["start_ms"],
                        kwargs["end_ms"],
                        kwargs["policy"],
                        kwargs["interval"],
                    ),
                )

    def test_text_entry_raises_input_error(self) -> None:
        # 文本入口（CSV）直接抛出 InputError，而非其他异常类型。
        with self.assertRaises(sr.InputError):
            sr.replay_text(
                "timestamp_ms,temperature,humidity\n-1,10,50\n"
            )

    def test_out_of_range_invalid_record_fails_text_entry(self) -> None:
        # 500 行落在 --start-ms 1000 区间外，整文件仍须先校验失败。
        text = (
            "timestamp_ms,temperature,humidity\n"
            "500,1e999,51\n"
            "1000,20,60\n"
        )
        with self.assertRaises(sr.InputError) as ctx:
            sr.replay_csv_text(text, start_ms=1000)
        # CSV 定位保留逻辑记录序号：表头第 1 条，首条数据为第 2 条。
        self.assertEqual(ctx.exception.record, 2)
        self.assertIsNone(ctx.exception.line)


class CliFailureLocationTest(unittest.TestCase):
    """会被筛选/抽样/重复策略丢弃的非法记录仍令命令行失败并保留定位。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp_dir = Path(self._tmp.name)

    def write_file(self, name: str, content: str) -> Path:
        path = self.tmp_dir / name
        path.write_text(content, encoding="utf-8")
        return path

    def assert_input_failure(
        self, result: subprocess.CompletedProcess, location: str
    ) -> None:
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn(location, result.stderr)
        self.assertIn("错误", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_csv_sampling_dropped_invalid_still_fails(self) -> None:
        # 500 组在 --min-interval-ms 1000 下本会被跳过（差值 500 < 1000），
        # 但其温度 1e999 非法，整次处理失败。
        path = self.write_file(
            "dropped.csv",
            "timestamp_ms,temperature,humidity\n"
            "0,10,50\n"
            "500,1e999,51\n"
            "1000,20,60\n",
        )
        result = run_cli(str(path), "--min-interval-ms", "1000")
        self.assert_input_failure(result, "第 3 条 CSV 记录")

    def test_csv_out_of_range_invalid_still_fails(self) -> None:
        path = self.write_file(
            "outside.csv",
            "timestamp_ms,temperature,humidity\n"
            "1500,1e999,51\n"
            "2000,20,60\n",
        )
        result = run_cli(str(path), "--start-ms", "2000")
        self.assert_input_failure(result, "第 2 条 CSV 记录")

    def test_jsonl_blank_line_counts_in_physical_line_number(self) -> None:
        # 第 1 行合法（ts 0）；第 2 行为空白行（计数但跳过解析）；
        # 第 3 行 ts 500 温度非法，本会被最小间隔抽样跳过，仍须失败，
        # 定位必须是包含空白行在内的物理行号 3。
        path = self.write_file(
            "lines.jsonl",
            '{"timestamp_ms":0,"temperature":10,"humidity":50}\n'
            "\n"
            '{"timestamp_ms":500,"temperature":"x","humidity":51}\n',
        )
        result = run_cli(
            str(path), "--format", "jsonl", "--min-interval-ms", "1000"
        )
        self.assert_input_failure(result, "第 3 行")

    def test_jsonl_text_entry_physical_line_number(self) -> None:
        text = (
            '{"timestamp_ms":0,"temperature":10,"humidity":50}\n'
            "\n"
            '{"timestamp_ms":500,"temperature":"x","humidity":51}'
        )
        with self.assertRaises(sr.InputError) as ctx:
            sr.replay_jsonl_text(text, min_interval_ms=1000)
        self.assertEqual(ctx.exception.line, 3)
        self.assertIsNone(ctx.exception.record)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
