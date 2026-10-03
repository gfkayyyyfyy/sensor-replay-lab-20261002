r"""JSONL 对象重复键规则的命令行回归测试：键名按 JSON 解码后的文本判重。

JSONL 入口对对象中的重复键一律拒绝——判定依据是 **JSON 解码后的键名文本**，
而非文件中的原始写法：同名键直接重复必须失败；原始写法与反斜杠 uXXXX 的
Unicode 转义写法不同、但解码后同名的情况同样必须失败（不允许后一个键覆盖
前一个键后继续回放）。重复值相同或不同都不影响拒绝结论。

为避免源码里的反斜杠 uXXXX 被上层 JSON 转义处理，本模块中的 Unicode 转义
写法统一在运行时由 ESC 常量（一个反斜杠）拼接十六进制码位得到，写入临时
文件的仍是标准 JSON 转义文本（如反斜杠 u0068 + "umidity"，解码为 humidity）。

本模块只通过子进程显式调用公开入口观察行为::

    python -m sensor_replay <临时文件路径> --format jsonl

核对退出码（失败固定为 2）、标准输出（失败时必须完全为空）、标准错误
（含“重复键”提示、解码后的字段名与物理行号，且无 Python 堆栈）。测试
自备小型 UTF-8 临时文件（用后即清），不依赖仓库演示文件，也不调用任何
内部解析函数代替公开入口；CSV 入口、跨行重复时间戳保留策略与缺测标记
均不在本模块覆盖范围内，维持原有测试与行为。

覆盖内容：

1. 三个采样字段 timestamp_ms / temperature / humidity 分别验证：
   - 同名键直接重复（重复值相同 / 不同各一）；
   - 一个原名与一个 Unicode 转义写法重复（值相同 / 不同各一）；
   - 另设一处两个键都用转义但转义位置不同、解码后仍同名的异值样本
     （源文本中不出现连续的解码字段名）。
2. 物理行号：第 1 行合法、第 2 行空行、第 3 行仅空格、第 4 行重复键，
   错误必须定位到第 4 行；叠加 --end-ms 0 后因整文件先逐行校验，
   仍报同一错误且第 1 行合法样本不得提前输出。
3. 合法对照：两个乱序源样本（1000 -> 20/50、0 -> 19/49），三个键在
   解码后各只出现一次且至少一处采用 Unicode 转义写法，字段顺序任意；
   回放成功，输出按 0、1000 排列，每行只含既有四个数值字段。

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

# 三个采样字段；每个重复样本只重复其中一处，其余两个字段各出现一次且合法。
FIELDS = ("timestamp_ms", "temperature", "humidity")

# 单行合法基线取值，以及“重复值不同”时第二个键使用的另一组合法取值。
BASE_VALUES = {"timestamp_ms": 1000, "temperature": 20, "humidity": 50}
OTHER_VALUES = {"timestamp_ms": 2000, "temperature": 21, "humidity": 51}

# 一个反斜杠字符；与四位十六进制码位拼接成 JSON 键的 Unicode 转义写法。
ESC = chr(92) + "u"


def escape_first_char(name: str) -> str:
    """把 name 首字符改写为反斜杠 uXXXX 转义，其余字符保持不变。

    例如 escape_first_char("humidity") 得到反斜杠 u0068 后接 "umidity"，
    该文本经 JSON 解码后仍是 "humidity"。
    """
    return ESC + f"{ord(name[0]):04x}" + name[1:]


def escape_second_char(name: str) -> str:
    """把 name 第二个字符改写为反斜杠 uXXXX 转义（用于换一个位置转义）。"""
    return name[0] + ESC + f"{ord(name[1]):04x}" + name[2:]


# 各字段“只转义首字母”的写法；JSON 解码后与原字段名完全相同。
ESCAPED_NAME = {name: escape_first_char(name) for name in FIELDS}


def build_duplicate_line(field: str, spelling: str, relation: str) -> str:
    """构造仅有一处重复键的单行 JSON。

    field 为重复的字段；spelling 为 "direct"（两处同名原写）或
    "unicode_escape"（第二处改为首字母转义写法）；relation 为 "same"
    （重复值相同）或 "different"（重复值不同）。其余两个字段保持合法。
    重复键紧跟在第一次出现的同名字段之后。
    """
    members: list[str] = []
    for name in FIELDS:
        members.append(f'"{name}":{BASE_VALUES[name]}')
        if name == field:
            duplicate_name = (
                name if spelling == "direct" else ESCAPED_NAME[name]
            )
            duplicate_value = (
                BASE_VALUES[name]
                if relation == "same"
                else OTHER_VALUES[name]
            )
            members.append(f'"{duplicate_name}":{duplicate_value}')
    return "{" + ",".join(members) + "}"


class JsonlDuplicateKeysTestCase(unittest.TestCase):
    """公共基类：自备 UTF-8 临时文件，以子进程调用 python -m sensor_replay。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp_dir = Path(self._tmp.name)

    def write_jsonl(self, lines: list[str], name: str = "samples.jsonl") -> Path:
        """把给定原始行写入小型 UTF-8 JSONL 临时文件（末尾带换行）。"""
        path = self.tmp_dir / name
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
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

    def assert_duplicate_key_error(
        self,
        result: subprocess.CompletedProcess,
        field: str,
        line_no: int = 1,
        forbid_raw_escape: bool = False,
    ) -> None:
        """核对重复键失败的既有约定：退出码 2、空 stdout、点名解码字段。"""
        self.assertEqual(result.returncode, 2, msg=f"stderr={result.stderr!r}")
        # 标准输出必须完全为空：任何合法样本都不得在校验失败前提前输出。
        self.assertEqual(result.stdout, "")
        self.assertIn(f"第 {line_no} 行", result.stderr)
        self.assertIn("重复键", result.stderr)
        # 错误信息必须点名 JSON 解码后的字段名（如 humidity）。
        self.assertIn(field, result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        if forbid_raw_escape:
            # 转义写法用例：标准错误不能残留任何反斜杠 uXXXX 原始写法，
            # 只能点名解码后的字段名。
            self.assertNotIn(ESC, result.stderr)

    @staticmethod
    def parse_output(stdout: str) -> list[dict]:
        """逐行解析 JSON Lines，确认每行仅含原有四个数值字段。"""
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


class TestJsonlDuplicateKeysRejected(JsonlDuplicateKeysTestCase):
    """三个采样字段的重复键一律拒绝，不因值相同或写法不同而被覆盖放行。"""

    def test_direct_and_escaped_duplicates_same_or_different_values(
        self,
    ) -> None:
        # 3 个字段 × 2 种重复写法（直接同名 / 原名 + Unicode 转义）
        # × 2 种值关系（相同 / 不同）= 12 个相互独立的单行样本。
        for field in FIELDS:
            for spelling in ("direct", "unicode_escape"):
                for relation in ("same", "different"):
                    with self.subTest(
                        field=field,
                        spelling=spelling,
                        values=relation,
                    ):
                        line = build_duplicate_line(
                            field, spelling, relation
                        )
                        path = self.write_jsonl([line])
                        result = self.run_jsonl(path)
                        self.assert_duplicate_key_error(
                            result,
                            field,
                            1,
                            forbid_raw_escape=(
                                spelling == "unicode_escape"
                            ),
                        )

    def test_two_different_escape_spellings_still_duplicate(self) -> None:
        # 两处出现都用 Unicode 转义，但转义位置不同：源文本中根本不包含
        # 连续的 "humidity"，解码后同名仍须按重复键拒绝（异值）。
        # 第一处转义首字母 h（码位 0068），第二处转义第二个字母 u
        # （码位 0075），两者解码后都是 humidity。
        first_spelling = ESCAPED_NAME["humidity"]
        second_spelling = escape_second_char("humidity")
        line = (
            '{"timestamp_ms":1000,"temperature":20,'
            f'"{first_spelling}":50,"{second_spelling}":51'
            "}"
        )
        # 防卫：原始写法确实不含解码后的完整字段名。
        self.assertNotIn("humidity", line)
        path = self.write_jsonl([line])
        result = self.run_jsonl(path)
        # 标准错误不得残留任何反斜杠 u 原始转义，只能点名解码后的 humidity。
        self.assert_duplicate_key_error(
            result, "humidity", 1, forbid_raw_escape=True
        )


class TestJsonlDuplicateKeyPhysicalLineNumber(JsonlDuplicateKeysTestCase):
    """空白行计入物理行号；区间筛选在整文件校验之后，不能掩盖重复键。"""

    LEGAL_FIRST_LINE = '{"timestamp_ms":0,"temperature":19,"humidity":49}'
    DUPLICATE_FOURTH_LINE = (
        '{"timestamp_ms":1000,"temperature":20,"humidity":50,"humidity":51}'
    )

    def build_file(self) -> Path:
        # 第 1 行：timestamp_ms=0 的合法样本；第 2 行：空行；
        # 第 3 行：仅含空格；第 4 行：humidity 直接重复（异值）。
        return self.write_jsonl(
            [
                self.LEGAL_FIRST_LINE,
                "",
                "   ",
                self.DUPLICATE_FOURTH_LINE,
            ]
        )

    def test_duplicate_after_blank_lines_reported_at_line_4(self) -> None:
        result = self.run_jsonl(self.build_file())
        self.assert_duplicate_key_error(result, "humidity", 4)

    def test_end_ms_0_still_reports_line_4_and_outputs_nothing(self) -> None:
        # --end-ms 0 本会在区间筛选时排除第 4 行（timestamp_ms=1000），
        # 但整文件先逐行校验：仍须报第 4 行重复键，且第 1 行合法样本
        # 不得在校验失败前被提前输出。
        result = self.run_jsonl(self.build_file(), "--end-ms", "0")
        self.assert_duplicate_key_error(result, "humidity", 4)


class TestJsonlUnicodeEscapedLegalKeys(JsonlDuplicateKeysTestCase):
    """合法对照：解码后三个键各出现一次（至少一处 Unicode 转义）。"""

    # 源记录刻意乱序且两行字段顺序不同；三个字段各至少有一次以反斜杠
    # uXXXX 转义书写，解码后每条记录三个键均只出现一次，不得误判为
    # 缺键或多键。
    SOURCE_LINES = [
        # 1000 -> 20/50：humidity、temperature 用首字母转义写法，
        # 键序 humidity 在前。
        (
            "{"
            f'"{ESCAPED_NAME["humidity"]}":50,'
            f'"{ESCAPED_NAME["temperature"]}":20,'
            '"timestamp_ms":1000}'
        ),
        # 0 -> 19/49：timestamp_ms 用首字母转义写法，其余原名。
        (
            "{"
            f'"{ESCAPED_NAME["timestamp_ms"]}":0,'
            '"temperature":19,"humidity":49}'
        ),
    ]

    EXPECTED_RECORDS = [
        {"timestamp_ms": 0, "elapsed_ms": 0, "temperature": 19, "humidity": 49},
        {"timestamp_ms": 1000, "elapsed_ms": 1000, "temperature": 20, "humidity": 50},
    ]

    EXPECTED_STDOUT = (
        '{"timestamp_ms":0,"elapsed_ms":0,"temperature":19,"humidity":49}\n'
        '{"timestamp_ms":1000,"elapsed_ms":1000,"temperature":20,"humidity":50}\n'
    )

    def test_escaped_names_decode_once_and_replay_succeeds(self) -> None:
        # 防卫：源文件确实采用了 Unicode 转义写法，而非全原名。
        self.assertIn(ESC, "\n".join(self.SOURCE_LINES))
        path = self.write_jsonl(self.SOURCE_LINES)
        result = self.run_jsonl(path)
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(result.stderr, "")
        # 输出按 0、1000 排列，elapsed_ms 为 0、1000，温湿度保持 19/49、
        # 20/50；逐行核对每行只含既有四个数值字段。
        self.assertEqual(result.stdout, self.EXPECTED_STDOUT)
        self.assertEqual(self.parse_output(result.stdout), self.EXPECTED_RECORDS)


if __name__ == "__main__":
    unittest.main()
