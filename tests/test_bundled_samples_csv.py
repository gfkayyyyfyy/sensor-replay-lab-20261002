"""随源码交付样本 ``samples.csv`` 的回归测试。

直接读取项目根目录自带的 ``samples.csv``（不自行创建或替换），先逐字节
核对文件内容（乱序与重复时间戳必须原样保留），再通过子进程调用公开入口
``python -m sensor_replay`` 核对基础回放与 ``--summary`` 两种公开输出。
文件缺失、样本内容或源顺序变化时本测试判定失败。

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

# 交付样本的约定内容：首行表头，随后 1000,20.5,60 / 0,19.5,55 / 1000,21,61，
# 乱序与两条不同数值的重复时间戳（1000）均原样保留，不排序、不去重、无空白行。
EXPECTED_TEXT = (
    "timestamp_ms,temperature,humidity\n"
    "1000,20.5,60\n"
    "0,19.5,55\n"
    "1000,21,61\n"
)

EXPECTED_REPLAY_LINES = [
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


def run_replay(*extra_args: str) -> subprocess.CompletedProcess:
    """以子进程运行 ``python -m sensor_replay samples.csv`` 并捕获结果。"""
    return subprocess.run(
        [sys.executable, "-m", "sensor_replay", "samples.csv", *extra_args],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


class TestBundledSamplesFile(unittest.TestCase):
    """交付文件本身：存在、UTF-8 可读、内容与源顺序逐字节一致。"""

    def test_file_exists_with_exact_content(self) -> None:
        self.assertTrue(
            SAMPLES_PATH.is_file(),
            msg="随源码交付的 samples.csv 缺失",
        )
        raw = SAMPLES_PATH.read_bytes()
        text = raw.decode("utf-8")  # 非法 UTF-8 时此处即失败
        self.assertNotIn(
            "\r", text, msg="samples.csv 应使用 LF 换行，每条记录占一行"
        )
        self.assertEqual(text, EXPECTED_TEXT)


class TestBundledSamplesReplay(unittest.TestCase):
    """基础回放：退出码 0、标准错误为空、恰好三行 JSON 且以换行结束。"""

    def test_replay_output(self) -> None:
        result = run_replay()
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertTrue(
            result.stdout.endswith("\n"), msg="标准输出必须以换行结尾"
        )
        lines = result.stdout.splitlines()
        self.assertEqual(len(lines), 3, msg="标准输出应恰好包含三行 JSON")
        records = [json.loads(line) for line in lines]
        self.assertEqual(records, EXPECTED_REPLAY_LINES)
        # 两条重复时间戳 1000 保持源文件先后顺序（20.5 在前，21 在后）。
        self.assertEqual(
            [r["temperature"] for r in records], [19.5, 20.5, 21]
        )
        self.assertEqual([r["humidity"] for r in records], [55, 60, 61])


class TestBundledSamplesSummary(unittest.TestCase):
    """--summary：唯一一个 JSON 对象加末尾换行，字段与极值与约定一致。"""

    def test_summary_output(self) -> None:
        result = run_replay("--summary")
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertTrue(
            result.stdout.endswith("\n"), msg="标准输出必须以换行结尾"
        )
        body = result.stdout[:-1]
        self.assertNotIn("\n", body, msg="摘要只能有一个 JSON 对象")
        summary = json.loads(body)
        self.assertEqual(summary, EXPECTED_SUMMARY)


if __name__ == "__main__":
    unittest.main()
