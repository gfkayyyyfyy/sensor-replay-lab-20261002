"""重复策略舍弃的非法样本仍触发整份输入校验的回归测试。

``--duplicate-policy first|last`` 会舍弃重复时间戳中的部分样本，但产品约定
是先校验整份输入、再应用保留策略：被舍弃样本中的非有限 temperature
（如 1e999 溢出为无穷）同样使整次回放失败——退出码 2、标准输出为空、
标准错误点名 temperature 且无 Traceback。同时固定合法对照：重复点均合法时
first/last 各自保留的完整记录及顺序。

测试自备临时小型离线文件，通过子进程调用 ``python -m sensor_replay``，
不依赖仓库演示文件。在项目根目录执行::

    python -m unittest discover -s tests
"""

from __future__ import annotations

import unittest

from test_replay_cli import HEADER, ReplayCliTestCase, run_replay

# 合法对照样本（源文件顺序）：1000 的两个重复点均合法。
VALID_ROWS = [
    "1000,20,50",
    "0,19,49",
    "1000,21,51",
    "2000,22,52",
]

# first 方向：第三条数据（重复组中将被 first 舍弃的一条）temperature 非法。
FIRST_INVALID_ROWS = [
    "1000,20,50",
    "0,19,49",
    "1000,1e999,51",
    "2000,22,52",
]

# last 方向：第一条数据（重复组中将被 last 舍弃的一条）temperature 非法。
LAST_INVALID_ROWS = [
    "1000,1e999,50",
    "0,19,49",
    "1000,21,51",
    "2000,22,52",
]


def csv_rows_to_jsonl(rows: list[str]) -> list[str]:
    """把 ``timestamp_ms,temperature,humidity`` 数据行转为 JSONL 物理行。"""
    lines = []
    for row in rows:
        timestamp_ms, temperature, humidity = row.split(",")
        lines.append(
            "{"
            f'"timestamp_ms":{timestamp_ms},'
            f'"temperature":{temperature},'
            f'"humidity":{humidity}'
            "}"
        )
    return lines


# 合法对照的完整输出记录（timestamp_ms/elapsed_ms/temperature/humidity）。
EXPECTED_FIRST = [
    {"timestamp_ms": 0, "elapsed_ms": 0, "temperature": 19, "humidity": 49},
    {"timestamp_ms": 1000, "elapsed_ms": 1000, "temperature": 20, "humidity": 50},
    {"timestamp_ms": 2000, "elapsed_ms": 2000, "temperature": 22, "humidity": 52},
]
EXPECTED_LAST = [
    {"timestamp_ms": 0, "elapsed_ms": 0, "temperature": 19, "humidity": 49},
    {"timestamp_ms": 1000, "elapsed_ms": 1000, "temperature": 21, "humidity": 51},
    {"timestamp_ms": 2000, "elapsed_ms": 2000, "temperature": 22, "humidity": 52},
]


class TestDiscardedInvalidTemperature(ReplayCliTestCase):
    """被 first/last 舍弃的重复点含非有限 temperature：整次回放仍失败。"""

    def assert_discarded_invalid(
        self, result, location: str
    ) -> None:
        """退出码 2、标准输出为空、标准错误含定位与 temperature 且无堆栈。"""
        self.assert_input_error(result)
        self.assertIn(location, result.stderr)
        self.assertIn("temperature", result.stderr)

    def test_csv_first(self) -> None:
        path = self.write_csv([HEADER, *FIRST_INVALID_ROWS])
        result = run_replay(path, "--duplicate-policy", "first")
        self.assert_discarded_invalid(result, "第 4 条 CSV 记录")

    def test_jsonl_first(self) -> None:
        path = self.write_jsonl(csv_rows_to_jsonl(FIRST_INVALID_ROWS))
        result = self.run_jsonl(path, "--duplicate-policy", "first")
        self.assert_discarded_invalid(result, "第 3 行")

    def test_csv_last(self) -> None:
        path = self.write_csv([HEADER, *LAST_INVALID_ROWS])
        result = run_replay(path, "--duplicate-policy", "last")
        self.assert_discarded_invalid(result, "第 2 条 CSV 记录")

    def test_jsonl_last(self) -> None:
        path = self.write_jsonl(csv_rows_to_jsonl(LAST_INVALID_ROWS))
        result = self.run_jsonl(path, "--duplicate-policy", "last")
        self.assert_discarded_invalid(result, "第 1 行")


class TestDiscardedValidControl(ReplayCliTestCase):
    """合法对照：重复点均合法时 first/last 各自保留的完整记录及顺序。"""

    def assert_policy_output(
        self, result, expected: list[dict]
    ) -> None:
        """退出码 0、标准错误为空，逐行解析后核对完整记录及顺序。"""
        records = self.assert_success(result)
        self.assertEqual(records, expected)

    def test_csv_first(self) -> None:
        path = self.write_csv([HEADER, *VALID_ROWS])
        result = run_replay(path, "--duplicate-policy", "first")
        self.assert_policy_output(result, EXPECTED_FIRST)

    def test_csv_last(self) -> None:
        path = self.write_csv([HEADER, *VALID_ROWS])
        result = run_replay(path, "--duplicate-policy", "last")
        self.assert_policy_output(result, EXPECTED_LAST)

    def test_jsonl_first(self) -> None:
        path = self.write_jsonl(csv_rows_to_jsonl(VALID_ROWS))
        result = self.run_jsonl(path, "--duplicate-policy", "first")
        self.assert_policy_output(result, EXPECTED_FIRST)

    def test_jsonl_last(self) -> None:
        path = self.write_jsonl(csv_rows_to_jsonl(VALID_ROWS))
        result = self.run_jsonl(path, "--duplicate-policy", "last")
        self.assert_policy_output(result, EXPECTED_LAST)


if __name__ == "__main__":
    unittest.main()
