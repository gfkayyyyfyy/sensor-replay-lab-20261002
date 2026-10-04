"""超大整数时间戳一毫秒区分度的命令行回归测试。

基准 B = 2**53 = 9007199254740992：超过该量级后相邻整数无法再用 IEEE-754
双精度浮点区分（如 B+1 与 B+2 会落到同一浮点值）。本测试回归既有行为——
timestamp_ms 始终按十进制**整数**逐毫秒处理，不设新的时间戳上限：

- 全量回放稳定排序后偏移为 0、1、1、2、3，elapsed_ms 为 0、1、1、2、3，
  相邻但不同的整数不合并，重复点保持源顺序；
- 闭区间 [B+1, B+2] 两端均包含，只保留三条，elapsed_ms 为 0、0、1；
- 全量范围同时使用 --min-interval-ms 2 与 --gap-threshold-ms 1 时只输出
  B 与 B+2，elapsed_ms 为 0、2，missing_before 为 false、true；
- 第一条记录时间戳写成 9007199254740995.0（浮点小数字面量）时，即使它落在
  所选区间之外，整次回放仍失败：退出码 2、标准输出为空、标准错误点名
  timestamp_ms 且无堆栈；CSV 定位第 2 条记录，JSONL 定位第 1 行。

测试自备小型 UTF-8 临时样本，通过子进程调用公开入口
``python -m sensor_replay``（CSV 走默认格式，JSONL 显式 --format jsonl），
核对退出码、标准输出与标准错误；预期值直接来自给定数据与规则，不调用产品
内部函数生成。回放为演示时钟立即回放，不需要真实等待。

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

# 基准 B = 2**53。以下 B+1、B+2、B+3 均为超出双精度浮点精确整数范围的
# 实际十进制整数，文件中逐字写出这些整数。
BASE = 9007199254740992
B0 = BASE
B1 = BASE + 1
B2 = BASE + 2
B3 = BASE + 3

# 五条记录的源文件顺序：时间偏移依次为 3、1、0、1、2（乱序且 B+1 重复）。
# 每项为 (timestamp_ms, temperature, humidity)。
SOURCE_RECORDS: list[tuple[int, int, int]] = [
    (B3, 23, 63),
    (B1, 21, 61),
    (B0, 20, 60),
    (B1, 22, 62),
    (B2, 24, 64),
]

# 全量回放的预期输出（稳定排序后）：
# (timestamp_ms, elapsed_ms, temperature, humidity)。
FULL_EXPECTED: list[tuple[int, int, int, int]] = [
    (B0, 0, 20, 60),
    (B1, 1, 21, 61),
    (B1, 1, 22, 62),
    (B2, 2, 24, 64),
    (B3, 3, 23, 63),
]

# 闭区间 [B+1, B+2] 的预期输出（两端均包含）。
INTERVAL_EXPECTED: list[tuple[int, int, int, int]] = [
    (B1, 0, 21, 61),
    (B1, 0, 22, 62),
    (B2, 1, 24, 64),
]

# 全量范围 + --min-interval-ms 2 + --gap-threshold-ms 1 的预期输出：
# (timestamp_ms, elapsed_ms, temperature, humidity, missing_before)。
SAMPLED_GAP_EXPECTED: list[tuple[int, int, int, int, bool]] = [
    (B0, 0, 20, 60, False),
    (B2, 2, 24, 64, True),
]

# 同上但省略缺测参数时的预期输出（只有原有四个字段）。
SAMPLED_EXPECTED: list[tuple[int, int, int, int]] = [
    (B0, 0, 20, 60),
    (B2, 2, 24, 64),
]

FOUR_KEYS = {"timestamp_ms", "elapsed_ms", "temperature", "humidity"}
FIVE_KEYS = FOUR_KEYS | {"missing_before"}

# 第一条记录（源顺序偏移 3）的非法浮点小数字面量。数值上恰为 B+3，
# 但带小数点的写法不满足“非负十进制整数”约束。
FLOAT_TIMESTAMP_LITERAL = "9007199254740995.0"
FLOAT_JSONL_LINE = (
    '{"timestamp_ms":9007199254740995.0,"temperature":23,"humidity":63}'
)

FORMATS = ("csv", "jsonl")


class LargeTimestampTestCase(unittest.TestCase):
    """公共基类：准备临时 UTF-8 CSV / JSONL 样本并运行命令行。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp_dir = Path(self._tmp.name)

    def write_csv(
        self,
        records: list[tuple[int, int, int]],
        name: str = "samples.csv",
    ) -> Path:
        lines = [HEADER]
        lines.extend(f"{ts},{temperature},{humidity}" for ts, temperature,
                     humidity in records)
        path = self.tmp_dir / name
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path

    def write_jsonl(
        self,
        records: list[tuple[int, int, int]],
        name: str = "samples.jsonl",
    ) -> Path:
        lines = [
            json.dumps(
                {
                    "timestamp_ms": ts,
                    "temperature": temperature,
                    "humidity": humidity,
                },
                separators=(",", ":"),
            )
            for ts, temperature, humidity in records
        ]
        path = self.tmp_dir / name
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path

    def write_samples(
        self,
        data_format: str,
        records: list[tuple[int, int, int]] = SOURCE_RECORDS,
    ) -> Path:
        if data_format == "csv":
            return self.write_csv(records)
        return self.write_jsonl(records)

    def run_replay(
        self, path: Path, data_format: str, *extra_args: str
    ) -> subprocess.CompletedProcess:
        """以子进程运行 ``python -m sensor_replay``。

        CSV 走默认格式（不传 --format），JSONL 显式指定 --format jsonl。
        """
        cmd = [sys.executable, "-m", "sensor_replay", str(path)]
        if data_format == "jsonl":
            cmd.extend(["--format", "jsonl"])
        cmd.extend(extra_args)
        return subprocess.run(
            cmd,
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )

    def replay_samples(
        self, data_format: str, *extra_args: str
    ) -> subprocess.CompletedProcess:
        path = self.write_samples(data_format)
        return self.run_replay(path, data_format, *extra_args)

    @staticmethod
    def parse_records(stdout: str) -> list[dict]:
        return [json.loads(line) for line in stdout.splitlines()]

    def assert_success(
        self,
        result: subprocess.CompletedProcess,
        expected_keys: set[str] = FOUR_KEYS,
    ) -> list[dict]:
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(result.stderr, "")
        records = self.parse_records(result.stdout)
        for record in records:
            self.assertEqual(set(record.keys()), expected_keys)
            # 时间戳与相对毫秒数均为 JSON 整数（Python int，且不是布尔）。
            self.assertIsInstance(record["timestamp_ms"], int)
            self.assertNotIsInstance(record["timestamp_ms"], bool)
            self.assertIsInstance(record["elapsed_ms"], int)
            self.assertNotIsInstance(record["elapsed_ms"], bool)
            if "missing_before" in record:
                self.assertIsInstance(record["missing_before"], bool)
        return records

    def assert_input_error(self, result: subprocess.CompletedProcess) -> None:
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertNotEqual(result.stderr, "")
        self.assertNotIn("Traceback", result.stderr)


class TestFullReplayOneMillisecondResolution(LargeTimestampTestCase):
    """全量回放：B 之上一毫秒级相邻整数仍被准确区分与排序。"""

    def test_both_formats_full_replay(self) -> None:
        outputs: dict[str, str] = {}
        for data_format in FORMATS:
            with self.subTest(format=data_format):
                result = self.replay_samples(data_format)
                records = self.assert_success(result)

                # 逐行 JSON 输出，恰好五条且末尾带换行。
                self.assertEqual(len(result.stdout.splitlines()), 5)
                self.assertTrue(result.stdout.endswith("\n"))

                self.assertEqual(
                    [
                        (
                            r["timestamp_ms"],
                            r["elapsed_ms"],
                            r["temperature"],
                            r["humidity"],
                        )
                        for r in records
                    ],
                    FULL_EXPECTED,
                )

                # 偏移 0、1、1、2、3：B+1 与 B+2 仅差 1 毫秒也不能合并；
                # 两个 B+1 重复点保持源顺序（21/61 在 22/62 之前）。
                self.assertEqual(
                    [r["timestamp_ms"] for r in records],
                    [B0, B1, B1, B2, B3],
                )
                self.assertEqual(
                    [r["elapsed_ms"] for r in records], [0, 1, 1, 2, 3]
                )
                distinct_timestamps = {r["timestamp_ms"] for r in records}
                self.assertEqual(distinct_timestamps, {B0, B1, B2, B3})
                self.assertEqual(
                    [r["temperature"] for r in records],
                    [20, 21, 22, 24, 23],
                )
                self.assertEqual(
                    [r["humidity"] for r in records], [60, 61, 62, 64, 63]
                )

                # 缺测参数省略时只有原有四个字段。
                for record in records:
                    self.assertNotIn("missing_before", record)

                # 超大整数必须逐字以十进制整数出现在输出中；任何浮点化
                # 都会丢精度或引入小数点/指数。
                for timestamp in (B0, B1, B2, B3):
                    self.assertIn(str(timestamp), result.stdout)
                self.assertNotIn(".", result.stdout)

                outputs[data_format] = result.stdout

        # CSV（默认格式）与显式 --format jsonl 的输出完全相同。
        self.assertEqual(outputs["csv"], outputs["jsonl"])


class TestClosedIntervalAroundLargeBase(LargeTimestampTestCase):
    """闭区间 [B+1, B+2]：两端均包含，只保留三条。"""

    def test_both_formats_closed_interval(self) -> None:
        outputs: dict[str, str] = {}
        for data_format in FORMATS:
            with self.subTest(format=data_format):
                result = self.replay_samples(
                    data_format,
                    "--start-ms",
                    str(B1),
                    "--end-ms",
                    str(B2),
                )
                records = self.assert_success(result)
                self.assertEqual(
                    [
                        (
                            r["timestamp_ms"],
                            r["elapsed_ms"],
                            r["temperature"],
                            r["humidity"],
                        )
                        for r in records
                    ],
                    INTERVAL_EXPECTED,
                )
                # elapsed_ms 从选中首条 B+1 重新计起：0、0、1。
                self.assertEqual(
                    [r["elapsed_ms"] for r in records], [0, 0, 1]
                )
                # 两个端点都包含；区间外的 B 与 B+3 不出现。
                self.assertEqual(
                    [r["timestamp_ms"] for r in records], [B1, B1, B2]
                )
                outputs[data_format] = result.stdout

        self.assertEqual(outputs["csv"], outputs["jsonl"])


class TestMinIntervalWithGapAtLargeBase(LargeTimestampTestCase):
    """全量范围叠加 --min-interval-ms 2 与 --gap-threshold-ms 1。"""

    def test_both_formats_sampling_and_gap(self) -> None:
        outputs: dict[str, str] = {}
        for data_format in FORMATS:
            with self.subTest(format=data_format):
                result = self.replay_samples(
                    data_format,
                    "--min-interval-ms",
                    "2",
                    "--gap-threshold-ms",
                    "1",
                )
                records = self.assert_success(result, FIVE_KEYS)
                # B 组始终保留；B+1 组与基准差 1 < 2 被跳过且不移动基准；
                # B+2 与基准 B 差 2（恰好相等）保留；末组 B+3 与 B+2 差
                # 1 < 2，不强行保留。
                self.assertEqual(
                    [
                        (
                            r["timestamp_ms"],
                            r["elapsed_ms"],
                            r["temperature"],
                            r["humidity"],
                            r["missing_before"],
                        )
                        for r in records
                    ],
                    SAMPLED_GAP_EXPECTED,
                )
                self.assertEqual(
                    [r["timestamp_ms"] for r in records], [B0, B2]
                )
                self.assertEqual([r["elapsed_ms"] for r in records], [0, 2])
                # 首条固定 false；B+2 - B = 2 严格大于 1 为 true。
                self.assertEqual(
                    [r["missing_before"] for r in records], [False, True]
                )
                self.assertEqual(
                    [(r["temperature"], r["humidity"]) for r in records],
                    [(20, 60), (24, 64)],
                )
                outputs[data_format] = result.stdout

        self.assertEqual(outputs["csv"], outputs["jsonl"])

    def test_both_formats_sampling_without_gap_keeps_four_fields(self) -> None:
        for data_format in FORMATS:
            with self.subTest(format=data_format):
                result = self.replay_samples(
                    data_format, "--min-interval-ms", "2"
                )
                records = self.assert_success(result, FOUR_KEYS)
                self.assertEqual(
                    [
                        (
                            r["timestamp_ms"],
                            r["elapsed_ms"],
                            r["temperature"],
                            r["humidity"],
                        )
                        for r in records
                    ],
                    SAMPLED_EXPECTED,
                )
                for record in records:
                    self.assertNotIn("missing_before", record)


class TestFloatTimestampLiteralRejected(LargeTimestampTestCase):
    """整数时间戳约束：9007199254740995.0 这类小数字面量必须整次失败。

    该记录时间戳为 B+3，即使选中区间 [B+1, B+2] 本会把它排除，整文件
    仍先校验，故整次回放失败。
    """

    def _interval_args(self) -> list[str]:
        return ["--start-ms", str(B1), "--end-ms", str(B2)]

    def test_csv_float_literal_fails_record_2(self) -> None:
        lines = [HEADER, f"{FLOAT_TIMESTAMP_LITERAL},23,63"]
        lines.extend(
            f"{ts},{temperature},{humidity}"
            for ts, temperature, humidity in SOURCE_RECORDS[1:]
        )
        path = self.tmp_dir / "samples.csv"
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")

        result = self.run_replay(path, "csv", *self._interval_args())
        self.assert_input_error(result)
        # 点名 timestamp_ms；表头为第 1 条 CSV 记录，首条数据定位第 2 条。
        self.assertIn("timestamp_ms", result.stderr)
        self.assertIn("第 2 条 CSV 记录", result.stderr)

    def test_jsonl_float_literal_fails_line_1(self) -> None:
        lines = [FLOAT_JSONL_LINE]
        lines.extend(
            json.dumps(
                {
                    "timestamp_ms": ts,
                    "temperature": temperature,
                    "humidity": humidity,
                },
                separators=(",", ":"),
            )
            for ts, temperature, humidity in SOURCE_RECORDS[1:]
        )
        path = self.tmp_dir / "samples.jsonl"
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        # JSONL 中该时间戳保留数值形式（无引号、带小数点），而非字符串。
        written = path.read_text(encoding="utf-8")
        self.assertIn('"timestamp_ms":9007199254740995.0', written)
        self.assertNotIn('"timestamp_ms":"9007199254740995.0"', written)

        result = self.run_replay(
            path, "jsonl", *self._interval_args()
        )
        self.assert_input_error(result)
        # 错误点名 timestamp_ms 且定位第 1 个物理行。
        self.assertIn("timestamp_ms", result.stderr)
        self.assertIn("第 1 行", result.stderr)


if __name__ == "__main__":
    unittest.main()
