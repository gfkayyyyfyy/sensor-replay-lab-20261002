"""大时间戳（2^53 基准）下毫秒级区分的命令行回归测试。

既有行为要求：以 B = 9007199254740992（2^53，IEEE 754 双精度恰好可表示
的最大整数）为基准的时间戳，相邻一毫秒差值仍须被准确区分——相邻但不同
的整数不能合并，重复点保持源顺序，timestamp_ms 与 elapsed_ms 均输出为
JSON 整数。本文件通过子进程调用公开入口 ``python -m sensor_replay``
（CSV 用默认格式，JSONL 显式 ``--format jsonl``），核对退出码、标准输出
与标准错误；预期结果直接由样本数据与既定规则推得，不调用产品内部函数。
测试自备临时 UTF-8 样本，回放为演示时钟立即回放，不等待真实采样间隔。

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

# 2^53：双精度浮点恰好可表示的最大整数，相邻整数仍可精确区分。
BASE = 9007199254740992

# 源文件顺序的五条记录：(相对 BASE 的偏移, temperature, humidity)。
# 乱序且偏移 1 重复出现两次（温湿度不同）。
SAMPLES = [
    (3, 23, 63),
    (1, 21, 61),
    (0, 20, 60),
    (1, 22, 62),
    (2, 24, 64),
]

EXPECTED_KEYS = {"timestamp_ms", "elapsed_ms", "temperature", "humidity"}
GAP_KEYS = EXPECTED_KEYS | {"missing_before"}

# 全量回放（稳定排序后）的预期：偏移 0、1、1、2、3。
FULL_OFFSETS = [0, 1, 1, 2, 3]
FULL_ELAPSED = [0, 1, 1, 2, 3]
FULL_TEMPERATURES = [20, 21, 22, 24, 23]
FULL_HUMIDITIES = [60, 61, 62, 64, 63]


def csv_rows() -> list[str]:
    """源顺序的 CSV 数据行，timestamp_ms 写成 BASE 加偏移后的实际整数。"""
    return [
        f"{BASE + offset},{temperature},{humidity}"
        for offset, temperature, humidity in SAMPLES
    ]


def jsonl_lines() -> list[str]:
    """与 CSV 等价的 JSONL 行（整数字面量时间戳，源顺序一致）。"""
    return [
        json.dumps(
            {
                "timestamp_ms": BASE + offset,
                "temperature": temperature,
                "humidity": humidity,
            },
            separators=(",", ":"),
        )
        for offset, temperature, humidity in SAMPLES
    ]


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


class LargeTimestampCliTestCase(unittest.TestCase):
    """公共基类：在临时目录中准备 CSV / JSONL 样本。"""

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

    def write_jsonl(
        self, lines: list[str], name: str = "samples.jsonl"
    ) -> Path:
        path = self.tmp_dir / name
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path

    def run_csv(
        self, path: Path, *extra_args: str
    ) -> subprocess.CompletedProcess:
        # CSV 为默认格式：不显式传 --format，与既有默认行为回归一致。
        return subprocess.run(
            [sys.executable, "-m", "sensor_replay", str(path), *extra_args],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )

    def run_jsonl(
        self, path: Path, *extra_args: str
    ) -> subprocess.CompletedProcess:
        return run_replay(path, *extra_args, data_format="jsonl")

    def parse_json_lines(
        self, stdout: str, expected_keys: set[str] = EXPECTED_KEYS
    ) -> list[dict]:
        """逐行解析 JSON Lines，校验字段集合与 JSON 整数类型。"""
        records = []
        for line in stdout.splitlines():
            record = json.loads(line)
            assert isinstance(record, dict)
            assert set(record.keys()) == expected_keys
            # 时间戳与相对毫秒数必须是 JSON 整数（解析后恰为 int，
            # 不是 bool 也不是 float）。
            for key in ("timestamp_ms", "elapsed_ms"):
                assert type(record[key]) is int
            for key in ("temperature", "humidity"):
                assert isinstance(record[key], (int, float))
                assert not isinstance(record[key], bool)
            if "missing_before" in record:
                assert type(record["missing_before"]) is bool
            records.append(record)
        return records

    def assert_success(
        self,
        result: subprocess.CompletedProcess,
        expected_keys: set[str] = EXPECTED_KEYS,
    ) -> list[dict]:
        """断言退出码为 0、标准错误为空，返回解析后的输出记录。"""
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(result.stderr, "")
        return self.parse_json_lines(result.stdout, expected_keys)

    def assert_input_error(self, result: subprocess.CompletedProcess) -> None:
        """断言退出码为 2、标准输出为空、标准错误非空且无堆栈。"""
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertNotEqual(result.stderr, "")
        self.assertNotIn("Traceback", result.stderr)

    def assert_full_replay(self, records: list[dict]) -> None:
        """全量回放的公共预期：一毫秒差值可区分，重复点保持源顺序。"""
        self.assertEqual(
            [r["timestamp_ms"] for r in records],
            [BASE + offset for offset in FULL_OFFSETS],
        )
        self.assertEqual(
            [r["elapsed_ms"] for r in records], FULL_ELAPSED
        )
        self.assertEqual(
            [r["temperature"] for r in records], FULL_TEMPERATURES
        )
        self.assertEqual(
            [r["humidity"] for r in records], FULL_HUMIDITIES
        )

    def assert_interval_replay(self, records: list[dict]) -> None:
        """闭区间 [B+1, B+2] 的公共预期：两端点均包含，共三条。"""
        self.assertEqual(
            [r["timestamp_ms"] for r in records],
            [BASE + 1, BASE + 1, BASE + 2],
        )
        self.assertEqual([r["elapsed_ms"] for r in records], [0, 0, 1])
        self.assertEqual([r["temperature"] for r in records], [21, 22, 24])
        self.assertEqual([r["humidity"] for r in records], [61, 62, 64])

    def assert_sampled_replay(self, records: list[dict]) -> None:
        """--min-interval-ms 2 与 --gap-threshold-ms 1 组合的公共预期。"""
        self.assertEqual(
            [r["timestamp_ms"] for r in records], [BASE, BASE + 2]
        )
        self.assertEqual([r["elapsed_ms"] for r in records], [0, 2])
        self.assertEqual([r["temperature"] for r in records], [20, 24])
        self.assertEqual([r["humidity"] for r in records], [60, 64])
        self.assertEqual(
            [r["missing_before"] for r in records], [False, True]
        )


class TestLargeTimestampCsv(LargeTimestampCliTestCase):
    """CSV（默认格式）下大时间戳的毫秒级区分。"""

    def test_full_replay_distinguishes_one_ms(self) -> None:
        path = self.write_csv(csv_rows())
        records = self.assert_success(self.run_csv(path))
        self.assert_full_replay(records)

    def test_closed_interval_keeps_both_endpoints(self) -> None:
        path = self.write_csv(csv_rows())
        result = self.run_csv(
            path, "--start-ms", str(BASE + 1), "--end-ms", str(BASE + 2)
        )
        self.assert_interval_replay(self.assert_success(result))

    def test_min_interval_and_gap_threshold(self) -> None:
        path = self.write_csv(csv_rows())
        result = self.run_csv(
            path, "--min-interval-ms", "2", "--gap-threshold-ms", "1"
        )
        self.assert_sampled_replay(self.assert_success(result, GAP_KEYS))

    def test_omitted_gap_flag_keeps_four_fields(self) -> None:
        path = self.write_csv(csv_rows())
        records = self.assert_success(self.run_csv(path), EXPECTED_KEYS)
        self.assertEqual(len(records), 5)


class TestLargeTimestampJsonl(LargeTimestampCliTestCase):
    """JSONL（显式 --format jsonl）下结果与 CSV 完全一致。"""

    def test_full_replay_distinguishes_one_ms(self) -> None:
        path = self.write_jsonl(jsonl_lines())
        records = self.assert_success(self.run_jsonl(path))
        self.assert_full_replay(records)

    def test_closed_interval_keeps_both_endpoints(self) -> None:
        path = self.write_jsonl(jsonl_lines())
        result = self.run_jsonl(
            path, "--start-ms", str(BASE + 1), "--end-ms", str(BASE + 2)
        )
        self.assert_interval_replay(self.assert_success(result))

    def test_min_interval_and_gap_threshold(self) -> None:
        path = self.write_jsonl(jsonl_lines())
        result = self.run_jsonl(
            path, "--min-interval-ms", "2", "--gap-threshold-ms", "1"
        )
        self.assert_sampled_replay(self.assert_success(result, GAP_KEYS))

    def test_omitted_gap_flag_keeps_four_fields(self) -> None:
        path = self.write_jsonl(jsonl_lines())
        records = self.assert_success(self.run_jsonl(path), EXPECTED_KEYS)
        self.assertEqual(len(records), 5)


class TestLargeTimestampFormatsAgree(LargeTimestampCliTestCase):
    """同一批样本在 CSV 与 JSONL 下的标准输出逐字节一致。"""

    def test_full_replay_outputs_identical(self) -> None:
        csv_result = self.run_csv(self.write_csv(csv_rows()))
        jsonl_result = self.run_jsonl(self.write_jsonl(jsonl_lines()))
        self.assertEqual(csv_result.returncode, 0, msg=csv_result.stderr)
        self.assertEqual(jsonl_result.returncode, 0, msg=jsonl_result.stderr)
        self.assertEqual(csv_result.stdout, jsonl_result.stdout)
        self.assertEqual(csv_result.stderr, jsonl_result.stderr)

    def test_interval_outputs_identical(self) -> None:
        bounds = ["--start-ms", str(BASE + 1), "--end-ms", str(BASE + 2)]
        csv_result = self.run_csv(self.write_csv(csv_rows()), *bounds)
        jsonl_result = self.run_jsonl(
            self.write_jsonl(jsonl_lines()), *bounds
        )
        self.assertEqual(csv_result.returncode, 0, msg=csv_result.stderr)
        self.assertEqual(jsonl_result.returncode, 0, msg=jsonl_result.stderr)
        self.assertEqual(csv_result.stdout, jsonl_result.stdout)

    def test_sampled_outputs_identical(self) -> None:
        flags = ["--min-interval-ms", "2", "--gap-threshold-ms", "1"]
        csv_result = self.run_csv(self.write_csv(csv_rows()), *flags)
        jsonl_result = self.run_jsonl(
            self.write_jsonl(jsonl_lines()), *flags
        )
        self.assertEqual(csv_result.returncode, 0, msg=csv_result.stderr)
        self.assertEqual(jsonl_result.returncode, 0, msg=jsonl_result.stderr)
        self.assertEqual(csv_result.stdout, jsonl_result.stdout)


class TestLargeTimestampFloatLiteralRejected(LargeTimestampCliTestCase):
    """大时间戳必须保持整数字面量：9007199254740995.0 一律整次失败。

    即使所选区间 [B+1, B+2] 不包含该记录，整文件校验仍先于区间筛选，
    退出码为 2、标准输出为空、标准错误点名 timestamp_ms 且无堆栈。
    """

    FLOAT_LITERAL = "9007199254740995.0"  # 即 (B+3).0 的小数写法
    BOUNDS = ["--start-ms", str(BASE + 1), "--end-ms", str(BASE + 2)]

    def test_csv_float_timestamp_rejected(self) -> None:
        rows = csv_rows()
        # 替换第一条数据记录（源顺序偏移 3，即 B+3）的时间戳字面量。
        assert rows[0].startswith(f"{BASE + 3},")
        rows[0] = f"{self.FLOAT_LITERAL},23,63"
        path = self.write_csv(rows)
        result = self.run_csv(path, *self.BOUNDS)
        self.assert_input_error(result)
        self.assertIn("timestamp_ms", result.stderr)
        # 表头算第 1 条 CSV 记录，首条数据为第 2 条。
        self.assertIn("第 2 条 CSV 记录", result.stderr)

    def test_jsonl_float_timestamp_rejected(self) -> None:
        lines = jsonl_lines()
        assert f'"timestamp_ms":{BASE + 3}' in lines[0]
        # JSONL 中保留数值形式（非字符串），仅把小数点写入字面量。
        lines[0] = lines[0].replace(
            f'"timestamp_ms":{BASE + 3}',
            f'"timestamp_ms":{self.FLOAT_LITERAL}',
        )
        path = self.write_jsonl(lines)
        result = self.run_jsonl(path, *self.BOUNDS)
        self.assert_input_error(result)
        self.assertIn("timestamp_ms", result.stderr)
        self.assertIn("第 1 行", result.stderr)


if __name__ == "__main__":
    unittest.main()
