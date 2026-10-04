"""随源码交付的 samples.csv 基础演示样本回归检查。

直接读取项目根目录的 ``samples.csv``（README“本地演示”一节引用的样本），
不自行创建或替换该文件：文件缺失、样本内容或源顺序发生变化时本测试失败，
避免用临时输入掩盖交付遗漏。

核对两种公开输出：

- ``python -m sensor_replay samples.csv``：三行 JSON Lines；
- ``python -m sensor_replay samples.csv --summary``：单个摘要 JSON 对象。

在项目根目录执行::

    python -m unittest discover -s tests
"""

from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

SAMPLES_PATH = PROJECT_ROOT / "samples.csv"

# 随源码交付的精确字节内容：UTF-8、LF、无 BOM、无空白记录；
# 数据故意乱序且保留两条数值不同的重复时间戳 1000。
EXPECTED_BYTES = (
    b"timestamp_ms,temperature,humidity\n"
    b"1000,20.5,60\n"
    b"0,19.5,55\n"
    b"1000,21,61\n"
)

EXPECTED_RECORDS = [
    {"timestamp_ms": 0, "elapsed_ms": 0, "temperature": 19.5, "humidity": 55},
    {"timestamp_ms": 1000, "elapsed_ms": 1000, "temperature": 20.5, "humidity": 60},
    {"timestamp_ms": 1000, "elapsed_ms": 1000, "temperature": 21, "humidity": 61},
]

EXPECTED_SUMMARY = {
    "sample_count": 3,
    "first_ms": 0,
    "last_ms": 1000,
    "duration_ms": 1000,
    "temperature": {"min": 19.5, "max": 21},
    "humidity": {"min": 55, "max": 61},
}

EXPECTED_KEYS = {"timestamp_ms", "elapsed_ms", "temperature", "humidity"}

SUMMARY_KEYS = {
    "sample_count",
    "first_ms",
    "last_ms",
    "duration_ms",
    "temperature",
    "humidity",
}


def run_samples(*extra_args: str) -> subprocess.CompletedProcess:
    """在项目根目录以子进程运行公开命令 ``python -m sensor_replay``。"""
    return subprocess.run(
        [sys.executable, "-m", "sensor_replay", "samples.csv", *extra_args],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


class TestShippedSamplesFile(unittest.TestCase):
    """samples.csv 必须直接随源码交付，且内容与源顺序固定。"""

    def test_file_is_shipped_with_exact_content(self) -> None:
        self.assertTrue(
            SAMPLES_PATH.is_file(),
            msg=f"随源码交付的样本缺失: {SAMPLES_PATH}",
        )
        # 字节级比较：UTF-8 文本、首行表头、随后三条乱序记录（含两条
        # 不同数值的重复时间戳）、每条记录占一行、以换行结束，无空白记录。
        self.assertEqual(SAMPLES_PATH.read_bytes(), EXPECTED_BYTES)

    def test_source_rows_are_unsorted_with_duplicate_timestamp(self) -> None:
        # 显式核对源文件先后顺序：不排序、不去重、不改写数值。
        text = SAMPLES_PATH.read_text(encoding="utf-8")
        self.assertEqual(
            text.splitlines(),
            [
                "timestamp_ms,temperature,humidity",
                "1000,20.5,60",
                "0,19.5,55",
                "1000,21,61",
            ],
        )


class TestShippedSamplesReplay(unittest.TestCase):
    """``python -m sensor_replay samples.csv`` 的公开回放输出。"""

    def test_basic_replay_output(self) -> None:
        result = run_samples()
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(result.stderr, "")
        # 恰好三行 JSON 对象，整体以换行结束。
        self.assertTrue(result.stdout.endswith("\n"))
        lines = result.stdout.splitlines()
        self.assertEqual(len(lines), 3)
        records = [json.loads(line) for line in lines]
        for record in records:
            self.assertEqual(set(record.keys()), EXPECTED_KEYS)
        self.assertEqual(records, EXPECTED_RECORDS)
        # 重复时间戳保持源文件先后顺序（20.5 在 21 之前）。
        self.assertEqual(
            [r["temperature"] for r in records if r["timestamp_ms"] == 1000],
            [20.5, 21],
        )

    def test_summary_output(self) -> None:
        result = run_samples("--summary")
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(result.stderr, "")
        # 只含一个 JSON 对象和末尾换行。
        self.assertTrue(result.stdout.endswith("\n"))
        self.assertEqual(len(result.stdout.splitlines()), 1)
        summary = json.loads(result.stdout)
        self.assertEqual(set(summary.keys()), SUMMARY_KEYS)
        self.assertEqual(set(summary["temperature"].keys()), {"min", "max"})
        self.assertEqual(set(summary["humidity"].keys()), {"min", "max"})
        self.assertEqual(summary, EXPECTED_SUMMARY)


if __name__ == "__main__":
    unittest.main()
