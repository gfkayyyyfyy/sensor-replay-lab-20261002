"""JSONL 重复键拒绝规则的命令行回归测试。

键名按 JSON 解码后的文本判重：同一对象中同名键直接重复，或原始写法与
Unicode 转义写法（如 ``humidity`` 与 ``\\u0068umidity``）解码后同名，都
必须拒绝，不允许后一个键覆盖前一个后继续回放。

测试自备临时 UTF-8 JSONL 文件，通过子进程显式调用
``python -m sensor_replay 文件路径 --format jsonl``，核对退出码、标准输出
和标准错误；不依赖仓库演示文件，也不调用内部解析函数。

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

EXPECTED_KEYS = {"timestamp_ms", "elapsed_ms", "temperature", "humidity"}

# 三个采样字段的合法取值（每条样本只安排一处重复键，其余字段保持合法）。
BASE_PAIRS = [("timestamp_ms", "1000"), ("temperature", "20"), ("humidity", "50")]

# 各字段重复出现的第二个取值（与 BASE_PAIRS 中不同，用于"重复值不同"用例）。
ALT_VALUE = {"timestamp_ms": "2000", "temperature": "21", "humidity": "51"}

# 各字段名首字母的 Unicode 转义写法：解码后与原始键名完全相同。
ESCAPED_KEY = {
    "timestamp_ms": "\\u0074imestamp_ms",
    "temperature": "\\u0074emperature",
    "humidity": "\\u0068umidity",
}


def make_line(pairs: list[tuple[str, str]]) -> str:
    """把 (键, 原始值字面量) 列表拼成一行 JSON 对象文本。"""
    return "{" + ",".join(f'"{key}":{value}' for key, value in pairs) + "}"


def run_jsonl(path: Path, *extra_args: str) -> subprocess.CompletedProcess:
    """以子进程运行 ``python -m sensor_replay <path> --format jsonl``。"""
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "sensor_replay",
            str(path),
            "--format",
            "jsonl",
            *extra_args,
        ],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


class DuplicateKeyTestCase(unittest.TestCase):
    """公共基类：临时目录、写文件与重复键错误断言。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp_dir = Path(self._tmp.name)

    def write_jsonl(self, lines: list[str], name: str = "samples.jsonl") -> Path:
        """把给定原始行写入临时 UTF-8 JSONL 文件（末尾带换行）。"""
        path = self.tmp_dir / name
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path

    def assert_duplicate_key_rejected(
        self,
        result: subprocess.CompletedProcess,
        field: str,
        line_no: int = 1,
    ) -> None:
        """断言重复键拒绝：退出码 2、标准输出为空、标准错误给出定位与
        解码后的字段名，且不泄漏 Python 堆栈。"""
        self.assertEqual(result.returncode, 2, msg=result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertNotEqual(result.stderr, "")
        self.assertNotIn("Traceback", result.stderr)
        self.assertIn(f"第 {line_no} 行", result.stderr)
        # 错误提示包含"重复键"及 JSON 解码后的字段名（repr 形式）。
        self.assertIn("重复键", result.stderr)
        self.assertIn(f"重复键: {field!r}", result.stderr)


class TestDirectDuplicateKeys(DuplicateKeyTestCase):
    """同名键直接重复：三个采样字段各自拒绝，重复值相同或不同均拒绝。"""

    def test_same_value_rejected(self) -> None:
        for field, _ in BASE_PAIRS:
            with self.subTest(field=field, variant="直接重复/同值"):
                pairs = BASE_PAIRS + [(field, dict(BASE_PAIRS)[field])]
                path = self.write_jsonl([make_line(pairs)])
                self.assert_duplicate_key_rejected(run_jsonl(path), field)

    def test_different_value_rejected(self) -> None:
        for field, _ in BASE_PAIRS:
            with self.subTest(field=field, variant="直接重复/不同值"):
                pairs = BASE_PAIRS + [(field, ALT_VALUE[field])]
                path = self.write_jsonl([make_line(pairs)])
                self.assert_duplicate_key_rejected(run_jsonl(path), field)


class TestEscapedDuplicateKeys(DuplicateKeyTestCase):
    """原始写法与 Unicode 转义写法解码后同名：同样按重复键拒绝。"""

    def assert_escaped_duplicate_rejected(
        self, pairs: list[tuple[str, str]], field: str
    ) -> None:
        path = self.write_jsonl([make_line(pairs)])
        result = run_jsonl(path)
        self.assert_duplicate_key_rejected(result, field)
        # 标准错误给出的是解码后的字段名，不出现原始转义写法。
        self.assertNotIn(ESCAPED_KEY[field], result.stderr)

    def test_raw_then_escaped_same_value(self) -> None:
        # 题目示例：{"timestamp_ms":1000,"temperature":20,"humidity":50,
        #           "humidity":50} 必须失败，不允许后者覆盖前者。
        for field, _ in BASE_PAIRS:
            with self.subTest(field=field, variant="原始+转义/同值"):
                pairs = BASE_PAIRS + [
                    (ESCAPED_KEY[field], dict(BASE_PAIRS)[field])
                ]
                self.assert_escaped_duplicate_rejected(pairs, field)

    def test_raw_then_escaped_different_value(self) -> None:
        for field, _ in BASE_PAIRS:
            with self.subTest(field=field, variant="原始+转义/不同值"):
                pairs = BASE_PAIRS + [
                    (ESCAPED_KEY[field], ALT_VALUE[field])
                ]
                self.assert_escaped_duplicate_rejected(pairs, field)

    def test_escaped_then_raw(self) -> None:
        # 转义写法在前、原始写法在后，解码后同名同样拒绝。
        for field, _ in BASE_PAIRS:
            with self.subTest(field=field, variant="转义+原始/不同值"):
                pairs = [
                    (ESCAPED_KEY[field], ALT_VALUE[field])
                    if key == field
                    else (key, value)
                    for key, value in BASE_PAIRS
                ]
                pairs = pairs + [(field, dict(BASE_PAIRS)[field])]
                self.assert_escaped_duplicate_rejected(pairs, field)


class TestDuplicateKeyLineLocation(DuplicateKeyTestCase):
    """空白行计入物理行号；区间外的重复键记录仍使整次回放失败。"""

    LINES = [
        '{"timestamp_ms":0,"temperature":19,"humidity":49}',
        "",
        "   ",
        '{"timestamp_ms":1000,"temperature":20,"humidity":50,'
        + '"\\u0068umidity":51}',
    ]

    def test_error_reports_line_4(self) -> None:
        # 第 1 行合法样本之后是空行与仅含空格的第三行，重复键记录在第 4 行。
        path = self.write_jsonl(self.LINES)
        result = run_jsonl(path)
        self.assert_duplicate_key_rejected(result, "humidity", line_no=4)

    def test_end_ms_zero_still_fails_without_partial_output(self) -> None:
        # 加 --end-ms 0 后第 4 行（时间戳 1000）落在区间之外：先整文件校验，
        # 仍报同一第 4 行错误；第 1 行合法样本不得提前输出。
        path = self.write_jsonl(self.LINES)
        result = run_jsonl(path, "--end-ms", "0")
        self.assert_duplicate_key_rejected(result, "humidity", line_no=4)


class TestEscapedKeysLegalControl(DuplicateKeyTestCase):
    """合法对照：解码后各键恰好出现一次（含 Unicode 转义写法）时正常回放。"""

    def test_escaped_keys_replay_successfully(self) -> None:
        # 两条源记录时间戳依次为 1000、0，温湿度 20/50、19/49；字段顺序任意，
        # 每条记录各有一个键采用 Unicode 转义写法，解码后无缺键也无多键。
        lines = [
            '{"\\u0074emperature":20,"timestamp_ms":1000,"humidity":50}',
            '{"humidity":49,"temperature":19,"\\u0074imestamp_ms":0}',
        ]
        path = self.write_jsonl(lines)
        result = run_jsonl(path)
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(result.stderr, "")

        records = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual(len(records), 2)
        for record in records:
            # 每条输出只含既有四个数值字段。
            self.assertEqual(set(record.keys()), EXPECTED_KEYS)
        # 输出按时间戳 0、1000 排列，elapsed_ms 为 0、1000。
        self.assertEqual(
            [r["timestamp_ms"] for r in records], [0, 1000]
        )
        self.assertEqual([r["elapsed_ms"] for r in records], [0, 1000])
        self.assertEqual([r["temperature"] for r in records], [19, 20])
        self.assertEqual([r["humidity"] for r in records], [49, 50])


if __name__ == "__main__":
    unittest.main()
