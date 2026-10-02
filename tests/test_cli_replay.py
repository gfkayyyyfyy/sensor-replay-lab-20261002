"""sensor_replay 命令行的回归测试。

通过 ``python -m sensor_replay`` 子进程核对退出码、标准输出和标准错误。
测试自备临时 UTF-8 CSV 文件，不依赖仓库中的样本文件，也不等待真实采样
间隔（回放本身为演示时钟立即回放）。

运行方式（项目根目录）::

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

EXPECTED_FIELDS = {"timestamp_ms", "elapsed_ms", "temperature", "humidity"}

# 成功样本：合法三列表头，数据记录按源文件顺序故意乱序且含重复时间戳。
SAMPLE_ROWS = [
    "2000,22,62",
    "1000,20.5,60",
    "0,19.5,55",
    "1000,21,61",
    "3000,23,63",
]
HEADER = "timestamp_ms,temperature,humidity"


def make_csv(rows: list[str]) -> str:
    return "\n".join([HEADER, *rows]) + "\n"


class SensorReplayCliTestCase(unittest.TestCase):
    """公共基座：临时目录、CSV 写入与 CLI 调用辅助。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="sensor_replay_test_")
        self.addCleanup(self._tmp.cleanup)
        self.tmp_dir = Path(self._tmp.name)

    def write_csv(self, content: str, name: str = "samples.csv") -> Path:
        path = self.tmp_dir / name
        path.write_text(content, encoding="utf-8", newline="")
        return path

    def run_cli(self, csv_path: Path, *args: str) -> "subprocess.CompletedProcess[bytes]":
        return subprocess.run(
            [sys.executable, "-m", "sensor_replay", str(csv_path), *args],
            capture_output=True,
            cwd=PROJECT_ROOT,
        )

    # -- 断言辅助 ---------------------------------------------------------

    def assert_json_lines(
        self,
        stdout: bytes,
        expected: list[tuple[int, int, float, float]],
    ) -> None:
        """标准输出逐行解析为仅含四个数值字段的 JSON 对象并核对取值。

        expected 元素为 (timestamp_ms, elapsed_ms, temperature, humidity)，
        顺序即输出行顺序；不对对象键序作任何限制。
        """
        text = stdout.decode("utf-8")
        lines = text.splitlines()
        self.assertEqual(
            len(lines), len(expected), f"输出行数不符，原始输出: {text!r}"
        )
        for line, (ts, elapsed, temp, hum) in zip(lines, expected):
            with self.subTest(line=line):
                obj = json.loads(line)
                self.assertIsInstance(obj, dict)
                self.assertEqual(set(obj), EXPECTED_FIELDS)
                for key, value in obj.items():
                    self.assertIsInstance(
                        value, (int, float), f"{key} 不是数值: {value!r}"
                    )
                    self.assertNotIsInstance(value, bool)
                self.assertEqual(obj["timestamp_ms"], ts)
                self.assertEqual(obj["elapsed_ms"], elapsed)
                self.assertEqual(obj["temperature"], temp)
                self.assertEqual(obj["humidity"], hum)

    def assert_success(
        self,
        result: "subprocess.CompletedProcess[bytes]",
        expected: list[tuple[int, int, float, float]],
    ) -> None:
        self.assertEqual(result.returncode, 0, f"stderr: {result.stderr!r}")
        self.assertEqual(result.stderr, b"", "成功时标准错误应为空")
        self.assert_json_lines(result.stdout, expected)

    def assert_empty_success(self, result: "subprocess.CompletedProcess[bytes]") -> None:
        self.assertEqual(result.returncode, 0, f"stderr: {result.stderr!r}")
        self.assertEqual(result.stdout, b"", "标准输出应为空")
        self.assertEqual(result.stderr, b"", "标准错误应为空")

    def assert_input_error(self, result: "subprocess.CompletedProcess[bytes]") -> None:
        self.assertEqual(result.returncode, 2, f"stdout: {result.stdout!r}")
        self.assertEqual(result.stdout, b"", "出错时标准输出应为空")
        self.assertNotEqual(result.stderr, b"", "出错时标准错误不应为空")
        self.assertNotIn(b"Traceback", result.stderr, "不应出现堆栈信息")


class TestIntervalReplay(SensorReplayCliTestCase):
    """成功路径：闭区间筛选、稳定排序与 elapsed_ms 计算。"""

    def setUp(self) -> None:
        super().setUp()
        self.csv_path = self.write_csv(make_csv(SAMPLE_ROWS))

    def test_interval_500_to_2000(self) -> None:
        result = self.run_cli(self.csv_path, "--start-ms", "500", "--end-ms", "2000")
        # 闭区间命中 1000、1000、2000；零点为选中记录的最早时间戳 1000。
        self.assert_success(
            result,
            [
                (1000, 0, 20.5, 60),
                (1000, 0, 21, 61),
                (2000, 1000, 22, 62),
            ],
        )

    def test_interval_1000_to_2000_same_result(self) -> None:
        result = self.run_cli(self.csv_path, "--start-ms", "1000", "--end-ms", "2000")
        self.assert_success(
            result,
            [
                (1000, 0, 20.5, 60),
                (1000, 0, 21, 61),
                (2000, 1000, 22, 62),
            ],
        )

    def test_equal_bounds_keep_duplicate_records(self) -> None:
        result = self.run_cli(self.csv_path, "--start-ms", "1000", "--end-ms", "1000")
        # 两端相等是合法区间；两条重复记录保留源文件顺序，elapsed_ms 均为零。
        self.assert_success(
            result,
            [
                (1000, 0, 20.5, 60),
                (1000, 0, 21, 61),
            ],
        )

    def test_start_only(self) -> None:
        result = self.run_cli(self.csv_path, "--start-ms", "2000")
        self.assert_success(
            result,
            [
                (2000, 0, 22, 62),
                (3000, 1000, 23, 63),
            ],
        )

    def test_end_only(self) -> None:
        result = self.run_cli(self.csv_path, "--end-ms", "1000")
        self.assert_success(
            result,
            [
                (0, 0, 19.5, 55),
                (1000, 1000, 20.5, 60),
                (1000, 1000, 21, 61),
            ],
        )

    def test_no_bounds_outputs_all_records_ascending(self) -> None:
        result = self.run_cli(self.csv_path)
        self.assert_success(
            result,
            [
                (0, 0, 19.5, 55),
                (1000, 1000, 20.5, 60),
                (1000, 1000, 21, 61),
                (2000, 2000, 22, 62),
                (3000, 3000, 23, 63),
            ],
        )

    def test_leading_zero_bounds_filter_as_integers(self) -> None:
        result = self.run_cli(self.csv_path, "--start-ms", "0500", "--end-ms", "02000")
        self.assert_success(
            result,
            [
                (1000, 0, 20.5, 60),
                (1000, 0, 21, 61),
                (2000, 1000, 22, 62),
            ],
        )


class TestEmptyOutput(SensorReplayCliTestCase):
    """正常结束但没有任何输出的两种情形。"""

    def test_interval_without_matching_records(self) -> None:
        csv_path = self.write_csv(make_csv(SAMPLE_ROWS))
        result = self.run_cli(csv_path, "--start-ms", "1500", "--end-ms", "1500")
        self.assert_empty_success(result)

    def test_header_only_file(self) -> None:
        csv_path = self.write_csv(HEADER + "\n")
        result = self.run_cli(csv_path)
        self.assert_empty_success(result)


class TestInvalidDataOutsideInterval(SensorReplayCliTestCase):
    """区间外的非法数据同样使整次输入失败，且标准输出完全为空。"""

    def test_nan_temperature_after_interval(self) -> None:
        # 追加的记录（第 7 条 CSV 记录）落在 500..2000 区间之外。
        csv_path = self.write_csv(make_csv([*SAMPLE_ROWS, "4000,NaN,70"]))
        result = self.run_cli(csv_path, "--start-ms", "500", "--end-ms", "2000")
        self.assert_input_error(result)
        stderr = result.stderr.decode("utf-8")
        self.assertIn("第 7 条 CSV 记录", stderr)
        self.assertIn("temperature", stderr)


class TestInvalidBounds(SensorReplayCliTestCase):
    """--start-ms / --end-ms 取值非法或区间倒置。"""

    def setUp(self) -> None:
        super().setUp()
        self.csv_path = self.write_csv(make_csv(SAMPLE_ROWS))

    def test_missing_value(self) -> None:
        for option in ("--start-ms", "--end-ms"):
            with self.subTest(option=option):
                result = self.run_cli(self.csv_path, option)
                self.assert_input_error(result)

    def test_signed_value(self) -> None:
        for token in ("--start-ms=-1", "--start-ms=+100", "--end-ms=-1"):
            with self.subTest(token=token):
                result = self.run_cli(self.csv_path, token)
                self.assert_input_error(result)

    def test_decimal_value(self) -> None:
        result = self.run_cli(self.csv_path, "--start-ms=10.5")
        self.assert_input_error(result)

    def test_exponent_value(self) -> None:
        result = self.run_cli(self.csv_path, "--end-ms=1e3")
        self.assert_input_error(result)

    def test_whitespace_value(self) -> None:
        for token in ("--start-ms= 1000", "--end-ms=1000 "):
            with self.subTest(token=token):
                result = self.run_cli(self.csv_path, token)
                self.assert_input_error(result)

    def test_empty_string_value(self) -> None:
        result = self.run_cli(self.csv_path, "--start-ms=")
        self.assert_input_error(result)

    def test_start_greater_than_end(self) -> None:
        result = self.run_cli(
            self.csv_path, "--start-ms", "2000", "--end-ms", "1000"
        )
        self.assert_input_error(result)


if __name__ == "__main__":
    unittest.main()
