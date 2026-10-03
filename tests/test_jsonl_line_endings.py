"""JSONL 物理行切分回归测试。

换行只认 LF、CRLF（计一次）与单独的 CR；U+000B、U+0085、U+2028 等其他
Unicode 行分隔符不断行、不增加行号，而是作为所在行的内容参与原有 JSON
校验（两个对象以此相连时按 JSON 语法错误拒绝；字符串内的 U+2028 不制造
新行）。空白行忽略但计数、末行无换行、BOM 等既有规则保留。

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

GOOD = '{"timestamp_ms":0,"temperature":1,"humidity":2}'


def run_jsonl(path: Path, *extra_args: str) -> subprocess.CompletedProcess:
    """以子进程运行 ``python -m sensor_replay --format jsonl`` 并捕获结果。"""
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


class JsonlLineEndingsTestCase(unittest.TestCase):
    """公共基类：在临时目录中按原始字节写 JSONL 文件。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp_dir = Path(self._tmp.name)

    def write_jsonl_bytes(self, text: str, name: str = "samples.jsonl") -> Path:
        """把文本以 UTF-8 字节写入临时文件（不追加任何换行）。"""
        path = self.tmp_dir / name
        path.write_bytes(text.encode("utf-8"))
        return path

    def assert_input_error(
        self, result: subprocess.CompletedProcess, line_no: int, fragment: str
    ) -> None:
        """断言退出码 2、标准输出为空、标准错误含行号与错误类别且无堆栈。"""
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn(f"第 {line_no} 行", result.stderr)
        self.assertIn(fragment, result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def assert_success(
        self, result: subprocess.CompletedProcess
    ) -> list[dict]:
        """断言退出码 0、标准错误为空，返回解析后的输出记录。"""
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(result.stderr, "")
        records = []
        for line in result.stdout.splitlines():
            record = json.loads(line)
            self.assertEqual(set(record.keys()), EXPECTED_KEYS)
            records.append(record)
        return records


class TestSpecialSeparators(JsonlLineEndingsTestCase):
    """U+000B/U+0085/U+2028 等字符不断行，作为行内容参与校验。"""

    def test_acceptance_vt_joined_objects_rejected_at_line_3(self) -> None:
        # 验收用例：首行合法、第二行只有空格、第三行由相同对象经一个实际
        # U+000B 连接，各行以 LF 分隔，文件末尾无换行。
        path = self.write_jsonl_bytes(
            GOOD + "\n   \n" + GOOD + "\v" + GOOD
        )
        result = run_jsonl(path)
        self.assert_input_error(result, 3, "JSON 语法错误")

    def test_acceptance_lf_variant_outputs_three_records(self) -> None:
        # 把第三行中间的 U+000B 改成 LF：三条记录，时间戳与回放时钟均为 0。
        path = self.write_jsonl_bytes(GOOD + "\n   \n" + GOOD + "\n" + GOOD)
        records = self.assert_success(run_jsonl(path))
        self.assertEqual(len(records), 3)
        for record in records:
            self.assertEqual(record["timestamp_ms"], 0)
            self.assertEqual(record["elapsed_ms"], 0)
            self.assertEqual(record["temperature"], 1)
            self.assertEqual(record["humidity"], 2)

    def test_special_chars_between_objects_are_syntax_errors(self) -> None:
        # 各种特殊分隔字符连接两个对象：同一物理行，按 JSON 语法错误拒绝。
        for char in ("\x0b", "\x0c", "\x85", "\u2028", "\u2029", "\x1c"):
            path = self.write_jsonl_bytes(GOOD + char + GOOD)
            result = run_jsonl(path)
            self.assert_input_error(result, 1, "JSON 语法错误")

    def test_special_chars_do_not_increment_line_number(self) -> None:
        # 首行内的特殊字符不占行号：第二物理行的错误仍报第 2 行。
        path = self.write_jsonl_bytes(
            GOOD + "\x0b" + GOOD + "\n" + GOOD + "\u2028" + GOOD
        )
        # 首行即因 Extra data 失败，行号为 1 而非被特殊字符推高。
        self.assert_input_error(run_jsonl(path), 1, "JSON 语法错误")

        bad_second = '{"timestamp_ms":1,"temperature":null,"humidity":2}'
        path = self.write_jsonl_bytes(
            GOOD + "\u2028\n" + bad_second  # U+2028 属第 1 行内容
        )
        # 第 1 行因对象后接 U+2028 语法错误，行号仍为 1。
        self.assert_input_error(run_jsonl(path), 1, "JSON 语法错误")

    def test_u2028_inside_string_stays_on_same_line(self) -> None:
        # 字符串内的 U+2028 不制造新行：temperature 字符串报该行的类型错误。
        line = '{"timestamp_ms":1,"temperature":"a\u2028b","humidity":2}'
        path = self.write_jsonl_bytes(GOOD + "\n" + line)
        result = run_jsonl(path)
        self.assert_input_error(result, 2, "temperature")
        self.assertIn("字符串", result.stderr)

    def test_u0085_only_line_is_blank_not_extra_lines(self) -> None:
        # 仅含 U+0085 等空白字符的行按空白行处理：忽略但计数。
        bad = '{"timestamp_ms":1,"temperature":null,"humidity":2}'
        path = self.write_jsonl_bytes(GOOD + "\n\x85\n" + bad)
        self.assert_input_error(run_jsonl(path), 3, "temperature")

    def test_invalid_special_char_record_outside_interval_fails(self) -> None:
        # 区间外或重复策略会舍弃的非法记录同样失败，--summary 不能绕过。
        bad = '{"timestamp_ms":9000,"temperature":1,"humidity":2}'
        joined = bad + "\x0b" + bad
        for extra in (
            ("--start-ms", "0", "--end-ms", "100"),
            ("--duplicate-policy", "first"),
            ("--duplicate-policy", "last"),
            ("--summary",),
        ):
            path = self.write_jsonl_bytes(GOOD + "\n" + joined)
            result = run_jsonl(path, *extra)
            self.assert_input_error(result, 2, "JSON 语法错误")


class TestLineBreakForms(JsonlLineEndingsTestCase):
    """LF、CRLF、单独 CR 三种换行；CRLF 只计一次。"""

    def test_lf_splits_lines(self) -> None:
        path = self.write_jsonl_bytes(GOOD + "\n" + GOOD + "\n" + GOOD)
        records = self.assert_success(run_jsonl(path))
        self.assertEqual(len(records), 3)

    def test_crlf_splits_lines_and_counts_once(self) -> None:
        path = self.write_jsonl_bytes(
            GOOD + "\r\n" + GOOD + "\r\n" + GOOD + "\r\n"
        )
        records = self.assert_success(run_jsonl(path))
        self.assertEqual(len(records), 3)
        # CRLF 计一次：其后的错误行号为 2 而非 3。
        bad = '{"timestamp_ms":1,"temperature":null,"humidity":2}'
        path = self.write_jsonl_bytes(GOOD + "\r\n" + bad)
        self.assert_input_error(run_jsonl(path), 2, "temperature")

    def test_cr_alone_splits_lines(self) -> None:
        path = self.write_jsonl_bytes(GOOD + "\r" + GOOD + "\r" + GOOD)
        records = self.assert_success(run_jsonl(path))
        self.assertEqual(len(records), 3)
        bad = '{"timestamp_ms":1,"temperature":null,"humidity":2}'
        path = self.write_jsonl_bytes(GOOD + "\r" + bad)
        self.assert_input_error(run_jsonl(path), 2, "temperature")

    def test_mixed_breaks_and_blank_line_counting(self) -> None:
        # 混合换行与空白行：空白行忽略但计数，末行无换行。
        bad = '{"timestamp_ms":1,"temperature":null,"humidity":2}'
        path = self.write_jsonl_bytes(
            GOOD + "\r\n" + "   " + "\n" + GOOD + "\r" + bad
        )
        self.assert_input_error(run_jsonl(path), 4, "temperature")

    def test_trailing_newline_does_not_add_blank_line(self) -> None:
        # 末尾换行（含 CRLF、CR）不产生额外空行，行为与既有规则一致。
        for ending in ("\n", "\r\n", "\r"):
            path = self.write_jsonl_bytes(GOOD + ending)
            records = self.assert_success(run_jsonl(path))
            self.assertEqual(len(records), 1)


if __name__ == "__main__":
    unittest.main()
