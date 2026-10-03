r"""JSONL 物理行切分规则的命令行回归测试：只认 LF / CRLF / 单独 CR。

JSONL 入口的物理行只按 LF、CRLF 与单独 CR 切分（CRLF 只计一次换行）。
其他 Unicode 行边界——U+000B（垂直制表符）、U+0085（NEL）、U+2028
（行分隔符）等——一律不视为换行，而是作为所在行的内容参与原有校验：
两个 JSON 对象以这类字符连接时按 JSON 语法错误拒绝；字符串值内部的
U+2028 不制造新行，字段类型校验仍报所在行的错误。空白行忽略但计入
物理行号、末行无换行、UTF-8（可带 BOM）等既有规则保持不变。

本模块只通过子进程显式调用公开入口观察行为::

    python -m sensor_replay <临时文件路径> --format jsonl

核对退出码（失败固定为 2）、标准输出（失败时必须完全为空）、标准错误
（含实际物理行号与相应错误类别，且无 Python 堆栈）。测试自备小型 UTF-8
临时文件（用后即清），不依赖仓库演示文件，也不调用任何内部解析函数代替
公开入口；CSV 入口与缺测标记等不在本模块覆盖范围内，维持原有测试与行为。

覆盖内容：

1. 特殊分隔字符：同一物理行中两个合法对象分别以 U+000B、U+0085、U+2028
   连接，均按该行的 JSON 语法错误拒绝（退出码 2、标准输出为空、标准错误
   含实际物理行号且无堆栈）；叠加 --end-ms 0 或 --summary 也不能绕过。
2. 字符串内的 U+2028：temperature 为含 U+2028 的字符串时不制造新行，
   仍报该行的类型错误（“字符串”），而非 JSON 语法错误。
3. 三种换行：LF、CRLF、单独 CR 分隔的相同样本均回放成功且输出一致；
   CRLF 只计一次换行（其后的错误行号不多计）。
4. 空白行计数：空行与仅空格行计入物理行号但不参与解析；末行无换行
   与开头 BOM 的既有行为不变。
5. 合法对照：第三行中间的 U+000B 换成 LF 后回放成功，输出三条
   timestamp_ms/elapsed_ms 均为 0、温度为 1、湿度为 2 的记录。

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

# 合法基线样本（验收用例使用的取值）。
OBJ = '{"timestamp_ms":0,"temperature":1,"humidity":2}'
# 时间戳不同的另一组合法样本，用于多行回放对照。
OBJ_1000 = '{"timestamp_ms":1000,"temperature":20,"humidity":50}'

# 不得视为换行的 Unicode 行边界字符（用 chr 显式写出码位，避免在源文件
# 中嵌入不可见字符）。
VT = chr(0x000B)  # 垂直制表符
NEL = chr(0x0085)  # NEL
LS = chr(0x2028)  # 行分隔符
BOM = chr(0xFEFF)  # 开头 BOM（解码后应被 utf-8-sig 去除）


class JsonlPhysicalLineTestCase(unittest.TestCase):
    """公共基类：自备 UTF-8 临时文件，以子进程调用 python -m sensor_replay。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp_dir = Path(self._tmp.name)

    def write_jsonl(self, content: str, name: str = "samples.jsonl") -> Path:
        """把给定原始文本按 UTF-8 写入临时文件（不做任何换行转换）。"""
        path = self.tmp_dir / name
        path.write_bytes(content.encode("utf-8"))
        return path

    def run_jsonl(
        self, path: Path, *extra_args: str
    ) -> subprocess.CompletedProcess:
        """显式以 ``python -m sensor_replay PATH --format jsonl`` 运行。"""
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

    def assert_json_syntax_error(
        self, result: subprocess.CompletedProcess, line_no: int
    ) -> None:
        """核对 JSON 语法错误失败的既有约定：退出码 2、空 stdout、定位行号。"""
        self.assertEqual(result.returncode, 2, msg=f"stderr={result.stderr!r}")
        # 标准输出必须完全为空：任何合法样本都不得在校验失败前提前输出。
        self.assertEqual(result.stdout, "")
        self.assertIn(f"第 {line_no} 行", result.stderr)
        self.assertIn("JSON 语法错误", result.stderr)
        self.assertNotIn("Traceback", result.stderr)


class TestSpecialSeparatorsRejected(JsonlPhysicalLineTestCase):
    """U+000B / U+0085 / U+2028 不视为换行，连接两个对象按语法错误拒绝。"""

    def test_objects_joined_by_special_chars_fail_at_physical_line(
        self,
    ) -> None:
        # 第 1 行合法、第 2 行仅空格、第 3 行为 对象+特殊字符+对象，
        # 各行以 LF 分隔，文件末尾无换行（即验收用例的布局）。
        for name, char in (("VT", VT), ("NEL", NEL), ("LS", LS)):
            with self.subTest(separator=name, codepoint=f"U+{ord(char):04X}"):
                content = OBJ + "\n" + "   " + "\n" + OBJ + char + OBJ
                path = self.write_jsonl(content)
                result = self.run_jsonl(path)
                self.assert_json_syntax_error(result, 3)

    def test_interval_filter_cannot_bypass_invalid_line(self) -> None:
        # --end-ms 本会在区间筛选时排除记录，但整文件先逐行校验：
        # 含 U+000B 的第 3 行仍须报语法错误，且标准输出为空。
        content = OBJ + "\n\n" + OBJ + VT + OBJ
        path = self.write_jsonl(content)
        result = self.run_jsonl(path, "--end-ms", "0")
        self.assert_json_syntax_error(result, 3)

    def test_summary_cannot_bypass_invalid_line(self) -> None:
        # --summary 同样不能绕过整文件校验。
        content = OBJ + "\n" + OBJ + LS + OBJ + "\n"
        path = self.write_jsonl(content)
        result = self.run_jsonl(path, "--summary")
        self.assert_json_syntax_error(result, 2)


class TestLineSeparatorInsideString(JsonlPhysicalLineTestCase):
    """字符串值内的 U+2028 不制造新行，仍报所在行的类型错误。"""

    def test_ls_inside_string_value_reports_type_error_on_same_line(self) -> None:
        # temperature 为含 U+2028 的字符串：若 U+2028 被当作换行，JSON 会
        # 因字符串未闭合而报语法错误；正确行为是整行解析后报类型错误。
        bad_line = '{"timestamp_ms":0,"temperature":"1' + LS + 'x","humidity":2}'
        content = OBJ_1000 + "\n" + bad_line + "\n" + OBJ
        path = self.write_jsonl(content)
        result = self.run_jsonl(path)
        self.assertEqual(result.returncode, 2, msg=f"stderr={result.stderr!r}")
        self.assertEqual(result.stdout, "")
        self.assertIn("第 2 行", result.stderr)
        self.assertIn("temperature", result.stderr)
        self.assertIn("字符串", result.stderr)
        self.assertNotIn("JSON 语法错误", result.stderr)
        self.assertNotIn("Traceback", result.stderr)


class TestRecognizedLineEndings(JsonlPhysicalLineTestCase):
    """LF、CRLF 与单独 CR 均为换行；CRLF 只计一次。"""

    def test_all_three_newline_styles_replay_identically(self) -> None:
        expected = (
            '{"timestamp_ms":0,"elapsed_ms":0,"temperature":1,"humidity":2}\n'
            '{"timestamp_ms":1000,"elapsed_ms":1000,'
            '"temperature":20,"humidity":50}\n'
        )
        for name, newline in (("LF", "\n"), ("CRLF", "\r\n"), ("CR", "\r")):
            with self.subTest(newline=name):
                # 末行无换行符。
                content = newline.join([OBJ, "", "   ", OBJ_1000])
                path = self.write_jsonl(content)
                result = self.run_jsonl(path)
                self.assertEqual(
                    result.returncode, 0, msg=f"stderr={result.stderr!r}"
                )
                self.assertEqual(result.stderr, "")
                self.assertEqual(result.stdout, expected)

    def test_crlf_counts_as_single_line_break(self) -> None:
        # 第 1 行合法，CRLF 之后第 2 行为 对象+VT+对象：若 CRLF 被计为
        # 两次换行，错误会被错报为第 3 行。
        content = OBJ + "\r\n" + OBJ + VT + OBJ
        path = self.write_jsonl(content)
        result = self.run_jsonl(path)
        self.assert_json_syntax_error(result, 2)

    def test_blank_lines_count_toward_physical_line_number(self) -> None:
        # 空行与仅空格行都计入物理行号：错误在第 5 行。
        content = "\n".join([OBJ, "", "   ", OBJ_1000, OBJ + NEL + OBJ])
        path = self.write_jsonl(content)
        result = self.run_jsonl(path)
        self.assert_json_syntax_error(result, 5)

    def test_bom_and_missing_final_newline_preserved(self) -> None:
        # 开头 BOM + 单独 CR 分隔 + 末行无换行：既有规则不变。
        content = BOM + OBJ + "\r" + OBJ_1000
        path = self.write_jsonl(content)
        result = self.run_jsonl(path)
        self.assertEqual(result.returncode, 0, msg=f"stderr={result.stderr!r}")
        self.assertEqual(result.stderr, "")
        records = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual(len(records), 2)
        for record in records:
            self.assertEqual(set(record.keys()), EXPECTED_KEYS)
        self.assertEqual(records[0]["timestamp_ms"], 0)
        self.assertEqual(records[1]["timestamp_ms"], 1000)


class TestAcceptanceScenario(JsonlPhysicalLineTestCase):
    """验收用例：bad.jsonl 第三行的 U+000B 换成 LF 后回放成功。"""

    def test_third_line_split_by_lf_yields_three_records(self) -> None:
        content = OBJ + "\n" + "   " + "\n" + OBJ + "\n" + OBJ
        path = self.write_jsonl(content, name="bad.jsonl")
        result = self.run_jsonl(path)
        self.assertEqual(result.returncode, 0, msg=f"stderr={result.stderr!r}")
        self.assertEqual(result.stderr, "")
        lines = result.stdout.splitlines()
        self.assertEqual(len(lines), 3)
        for line in lines:
            record = json.loads(line)
            self.assertEqual(set(record.keys()), EXPECTED_KEYS)
            self.assertEqual(record["timestamp_ms"], 0)
            self.assertEqual(record["elapsed_ms"], 0)
            self.assertEqual(record["temperature"], 1)
            self.assertEqual(record["humidity"], 2)


if __name__ == "__main__":
    unittest.main()
