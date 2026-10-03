"""被重复点保留策略舍弃的非法样本仍触发整份输入校验的命令行回归测试。

``--duplicate-policy first|last`` 只在**整份输入校验通过**后才生效：同一
timestamp_ms 组中本会被策略舍弃的那条样本若温度为非有限值（如溢出为
无穷的 ``1e999``），整次回放仍须按既有输入错误约定失败——退出码 2、
标准输出完全为空、标准错误点名 temperature 并给出原有定位（CSV 记录
序号/JSONL 物理行号），且无 Python 堆栈。

同时为每种方向与格式提供四条样本的合法对照：重复点合法时，first/last
分别保留源顺序中最先/最后的一条，输出逐行解析后仅含原有四个数值字段，
完整记录与顺序固定。

测试通过子进程调用公开入口 ``python -m sensor_replay``，只观察退出码、
标准输出与标准错误；样本由测试自行写入小型临时文件（结束后清理），不
依赖仓库演示文件，也不设置区间或缺测参数。

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

EXPECTED_KEYS = {"timestamp_ms", "elapsed_ms", "temperature", "humidity"}

# --- 非法样本：重复点中混入 1e999（解析为无穷）-----------------------------
# 源顺序四条：1000 出现两次（不相邻），中间夹 0 与 2000。
# first 方向：非有限温度在第三条数据（1000 组中后出现、first 会舍弃的一条）。
FIRST_INVALID_CSV_ROWS = [
    "1000,20,50",
    "0,19,49",
    "1000,1e999,51",
    "2000,22,52",
]
# last 方向：非有限温度在第一条数据（1000 组中先出现、last 会舍弃的一条），
# 第三条数据改回合法的 1000,21,51。
LAST_INVALID_CSV_ROWS = [
    "1000,1e999,50",
    "0,19,49",
    "1000,21,51",
    "2000,22,52",
]

# JSONL 与 CSV 同源于相同顺序、相同三个键，每条占一个物理行；1e999 必须
# 以原始字面量写入（json.dumps 无法序列化无穷）。
FIRST_INVALID_JSONL_LINES = [
    '{"timestamp_ms":1000,"temperature":20,"humidity":50}',
    '{"timestamp_ms":0,"temperature":19,"humidity":49}',
    '{"timestamp_ms":1000,"temperature":1e999,"humidity":51}',
    '{"timestamp_ms":2000,"temperature":22,"humidity":52}',
]
LAST_INVALID_JSONL_LINES = [
    '{"timestamp_ms":1000,"temperature":1e999,"humidity":50}',
    '{"timestamp_ms":0,"temperature":19,"humidity":49}',
    '{"timestamp_ms":1000,"temperature":21,"humidity":51}',
    '{"timestamp_ms":2000,"temperature":22,"humidity":52}',
]

# --- 合法对照：两个重复点固定为 1000,20,50 与 1000,21,51 -------------------
LEGAL_CSV_ROWS = [
    "1000,20,50",
    "0,19,49",
    "1000,21,51",
    "2000,22,52",
]
LEGAL_JSONL_OBJECTS = [
    {"timestamp_ms": 1000, "temperature": 20, "humidity": 50},
    {"timestamp_ms": 0, "temperature": 19, "humidity": 49},
    {"timestamp_ms": 1000, "temperature": 21, "humidity": 51},
    {"timestamp_ms": 2000, "temperature": 22, "humidity": 52},
]

# 排序去重后的完整记录（含顺序）；时间戳与 elapsed_ms 均为 0、1000、2000。
FIRST_EXPECTED_RECORDS = [
    {"timestamp_ms": 0, "elapsed_ms": 0, "temperature": 19, "humidity": 49},
    {"timestamp_ms": 1000, "elapsed_ms": 1000, "temperature": 20, "humidity": 50},
    {"timestamp_ms": 2000, "elapsed_ms": 2000, "temperature": 22, "humidity": 52},
]
LAST_EXPECTED_RECORDS = [
    {"timestamp_ms": 0, "elapsed_ms": 0, "temperature": 19, "humidity": 49},
    {"timestamp_ms": 1000, "elapsed_ms": 1000, "temperature": 21, "humidity": 51},
    {"timestamp_ms": 2000, "elapsed_ms": 2000, "temperature": 22, "humidity": 52},
]


class DuplicateDroppedInvalidTestCase(unittest.TestCase):
    """公共基类：自备临时文件并以子进程调用 python -m sensor_replay。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp_dir = Path(self._tmp.name)

    def write_csv(self, rows: list[str]) -> Path:
        path = self.tmp_dir / "samples.csv"
        path.write_text(
            HEADER + "\n" + "\n".join(rows) + "\n", encoding="utf-8"
        )
        return path

    def write_jsonl_lines(self, lines: list[str]) -> Path:
        path = self.tmp_dir / "samples.jsonl"
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path

    def write_jsonl_objects(self, records: list[dict]) -> Path:
        path = self.tmp_dir / "samples.jsonl"
        path.write_text(
            "\n".join(json.dumps(r, separators=(",", ":")) for r in records)
            + "\n",
            encoding="utf-8",
        )
        return path

    def run_replay(
        self, path: Path, data_format: str, policy: str
    ) -> subprocess.CompletedProcess:
        return subprocess.run(
            [
                sys.executable,
                "-m",
                "sensor_replay",
                str(path),
                "--format",
                data_format,
                "--duplicate-policy",
                policy,
            ],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )

    @staticmethod
    def parse_output(stdout: str) -> list[dict]:
        """逐行解析 JSON Lines，并确认每行仅含原有四个数值字段。"""
        records = []
        for line in stdout.splitlines():
            record = json.loads(line)
            assert isinstance(record, dict)
            assert set(record.keys()) == EXPECTED_KEYS
            for value in record.values():
                assert isinstance(value, (int, float))
                assert not isinstance(value, bool)
            records.append(record)
        return records

    def assert_nonfinite_dropped_record_fails(
        self, result: subprocess.CompletedProcess, location: str
    ) -> None:
        """被策略舍弃的非有限温度仍使整次回放失败（固定既有校验约定）。"""
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertNotEqual(result.stderr, "")
        self.assertIn("temperature", result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        self.assertIn(location, result.stderr)

    def assert_legal_control(
        self, data_format: str, policy: str, expected: list[dict]
    ) -> None:
        """合法对照：退出 0、标准错误为空，完整记录与顺序逐行核对。"""
        if data_format == "csv":
            path = self.write_csv(LEGAL_CSV_ROWS)
        else:
            path = self.write_jsonl_objects(LEGAL_JSONL_OBJECTS)
        result = self.run_replay(path, data_format, policy)
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(result.stderr, "")
        records = self.parse_output(result.stdout)
        self.assertEqual(records, expected)


class TestDroppedNonfiniteRecordStillFails(DuplicateDroppedInvalidTestCase):
    """重复点中的非有限温度即使会被策略舍弃，也先使整份输入失败。"""

    def test_csv_first(self) -> None:
        # 表头算第 1 条 CSV 记录，第三条数据即第 4 条。
        path = self.write_csv(FIRST_INVALID_CSV_ROWS)
        result = self.run_replay(path, "csv", "first")
        self.assert_nonfinite_dropped_record_fails(result, "第 4 条 CSV 记录")

    def test_csv_last(self) -> None:
        # 非有限温度在第一条数据，即第 2 条 CSV 记录。
        path = self.write_csv(LAST_INVALID_CSV_ROWS)
        result = self.run_replay(path, "csv", "last")
        self.assert_nonfinite_dropped_record_fails(result, "第 2 条 CSV 记录")

    def test_jsonl_first(self) -> None:
        # 每条对象一个物理行：非有限温度在第 3 行。
        path = self.write_jsonl_lines(FIRST_INVALID_JSONL_LINES)
        result = self.run_replay(path, "jsonl", "first")
        self.assert_nonfinite_dropped_record_fails(result, "第 3 行")

    def test_jsonl_last(self) -> None:
        # 非有限温度在第 1 行。
        path = self.write_jsonl_lines(LAST_INVALID_JSONL_LINES)
        result = self.run_replay(path, "jsonl", "last")
        self.assert_nonfinite_dropped_record_fails(result, "第 1 行")


class TestDedupLegalControl(DuplicateDroppedInvalidTestCase):
    """相同输入布局的合法对照：first/last 在两种格式下输出固定。"""

    def test_csv_first(self) -> None:
        self.assert_legal_control("csv", "first", FIRST_EXPECTED_RECORDS)

    def test_csv_last(self) -> None:
        self.assert_legal_control("csv", "last", LAST_EXPECTED_RECORDS)

    def test_jsonl_first(self) -> None:
        self.assert_legal_control("jsonl", "first", FIRST_EXPECTED_RECORDS)

    def test_jsonl_last(self) -> None:
        self.assert_legal_control("jsonl", "last", LAST_EXPECTED_RECORDS)


if __name__ == "__main__":
    unittest.main()
