# 传感器数据回放实验台

建设用于教学和接口联调的本地传感器数据回放产品，逐步覆盖 CSV 或 JSONL 采样导入、通道定义、时间轴查询、按时间区间回放、暂停与续播、简单重采样、缺测提示和统计导出。

计划采用：Python 3 标准库 / csv / json / datetime / argparse。

当前版本交付最小可运行功能：**CSV / JSONL 温湿度样本的命令行即时回放**（仅依赖 Python 3 标准库，无需安装）。用 `--format csv|jsonl` 选择输入格式，默认 `csv`，不按文件扩展名推断。暂停续播、通道配置和统计导出留待后续版本。

## 运行环境

- Python 3（仅使用标准库）
- 无需创建虚拟环境或安装依赖

## 公开命令

```bash
python -m sensor_replay <文件路径> [--format csv|jsonl] [--start-ms MS] [--end-ms MS]
```

- 文件路径是唯一必填参数，**支持带空格的路径**（用引号包裹即可）：

  ```bash
  python -m sensor_replay "my data/demo.csv"
  ```

- `--format csv|jsonl`：选择样本格式，默认 `csv`，**不按文件扩展名推断**
  （把 JSONL 内容存成 `.csv` 仍按 CSV 解析，反之亦然）。

  ```bash
  python -m sensor_replay demo.jsonl --format jsonl
  ```

- `--start-ms MS` / `--end-ms MS`：可选的**时间区间回放**，只输出 `timestamp_ms`
  落在闭区间 `[start, end]` 内的原始样本（两个端点都包含，两端相等是合法区间）。
  两者可单独使用，省略的一端表示不限制；全部省略时保持全量回放。
  取值只接受非负十进制整数（允许前导零，不接受符号、小数、指数或空白）。
  区间外的非法数据不会被忽略，仍使整个输入失败。
- 查看帮助：

  ```bash
  python -m sensor_replay --help
  ```

## 输入文件格式

两种格式均使用 UTF-8 编码，**允许文件开头带 BOM**。

### CSV（`--format csv`，默认）

- 首行为表头，必须恰好包含三列：`timestamp_ms`、`temperature`、`humidity`。
  - 列顺序可以任意；
  - 不接受额外列或重复列。
- `timestamp_ms`：从某个起点开始的毫秒数，只接受**非负十进制整数**（不接受负号、小数、空白）。
- `temperature`、`humidity`：有限数值，允许小数、负数和指数写法；不施加物理范围限制；`NaN`、`Infinity` 及溢出为无穷的值一律拒绝。
- 数据行顺序任意，允许乱序和重复时间戳。

### JSONL（`--format jsonl`）

- 每个**非空物理行**恰好是一个 JSON 对象；**空白行被忽略但仍计入物理行号**；末行可以没有换行符。
- 对象**仅含** `timestamp_ms`、`temperature`、`humidity` 三个键；缺键、多键或重复键均拒绝。
- `timestamp_ms`：只接受**不带负号的 JSON 整数字面量**（如 `0`、`1000`）；拒绝负数（包括 `-0`）、小数（`1.0`）和指数（`1e3`）。
- `temperature`、`humidity`：接受**有限 JSON 数值**，允许负数、小数和指数（`-3.5`、`1e2`），不施加物理范围限制；字符串、布尔值与 `null` 一律拒绝，`NaN`、`Infinity`、`-Infinity` 以及浮点溢出（如 `1e999`）一律拒绝。
- 三个字段都拒绝字符串、布尔值与 `null`。
- 行会被完整校验：语法错误、一行多个对象、非对象行（数组、标量）均拒绝。
- 行顺序任意，允许乱序和重复时间戳。

## 回放与输出规则

- 按 `timestamp_ms` 升序回放；时间戳重复时保留全部记录，并维持它们在源文件中的先后顺序（稳定排序）。
- 使用**演示时钟立即回放**：排序后第一条样本的回放时间为零，后续样本按其时间戳与首条样本的差值推进，即 `elapsed_ms = timestamp_ms - 首条时间戳`。指定区间时，零点为**选中记录**的最早时间戳，而非参数起点或被排除的样本。
- 没有样本落入区间时正常结束：标准输出为空，退出码 `0`。
- 标准输出逐行输出 JSON（JSON Lines），每行只含四个数值字段：

  ```json
  {"timestamp_ms":0,"elapsed_ms":0,"temperature":19.5,"humidity":55}
  ```

- 忠实保留每条样本的温湿度数值，不插值、不平均。
- 正常结束退出码为 `0`。

## 本地演示

仓库自带样本文件 `samples.csv`，内容如下（注意输入是乱序的，且存在重复时间戳）：

```csv
timestamp_ms,temperature,humidity
1000,20.5,60
0,19.5,55
1000,21,61
```

在仓库根目录执行：

```bash
python -m sensor_replay samples.csv
```

预期标准输出（退出码 `0`）：

```json
{"timestamp_ms":0,"elapsed_ms":0,"temperature":19.5,"humidity":55}
{"timestamp_ms":1000,"elapsed_ms":1000,"temperature":20.5,"humidity":60}
{"timestamp_ms":1000,"elapsed_ms":1000,"temperature":21,"humidity":61}
```

即：按时间戳升序依次输出温度 19.5、20.5、21；`elapsed_ms` 依次为 0、1000、1000；湿度依次为 55、60、61；两条 `timestamp_ms = 1000` 的记录保持源文件先后顺序。

仓库另自带 JSONL 样本 `demo.jsonl`（三行对象，乱序且含重复时间戳）：

```jsonl
{"timestamp_ms":2000,"temperature":22,"humidity":62}
{"timestamp_ms":1000,"temperature":20.5,"humidity":60}
{"timestamp_ms":1000,"temperature":21,"humidity":61}
```

在仓库根目录执行：

```bash
python -m sensor_replay demo.jsonl --format jsonl --start-ms 500 --end-ms 2000
```

预期标准输出（退出码 `0`，标准错误为空）：

```json
{"timestamp_ms":1000,"elapsed_ms":0,"temperature":20.5,"humidity":60}
{"timestamp_ms":1000,"elapsed_ms":0,"temperature":21,"humidity":61}
{"timestamp_ms":2000,"elapsed_ms":1000,"temperature":22,"humidity":62}
```

## 错误处理与退出码

以下情况均为输入错误：**标准输出保持为空**，错误信息写入**标准错误**，进程以退出码 **`2`** 结束，不输出堆栈：

- 空文件；
- 表头不合法（缺列、额外列、重复列、列名错误）；
- 数据行列数与表头不符；
- `timestamp_ms` 非法（负数、小数、缺失、非数字等）；
- `temperature` / `humidity` 缺失、无法解析，或为 `NaN`、无穷值；
- JSONL 行不是合法 JSON、不是 JSON 对象、一行含多个对象；
- JSONL 对象缺键、多键或存在重复键；
- JSONL `timestamp_ms` 为负数（含 `-0`）、小数、指数，或类型为字符串、布尔值、`null`；
- JSONL `temperature` / `humidity` 为字符串、布尔值、`null`、`NaN`、`Infinity`，或浮点溢出；
- `--start-ms` / `--end-ms` 缺值或格式非法（符号、小数、指数、空白、空值），或起点大于终点；
- `--format` 缺值或取值不在 `csv` / `jsonl` 中；
- 文件不存在、是目录、无法读取，或不是有效 UTF-8 编码。

数据记录错误会注明位置：

- CSV 模式注明 **CSV 记录序号**（表头算第 1 条，其后的数据行依次为第 2、3……条）；
- JSONL 模式注明**从 1 开始的物理行号**，空白行也计数。

例如：

```text
错误: 第 3 条 CSV 记录: 数据行列数不符：应为 3 列，实际为 2 列
错误: 第 4 行: temperature 必须是有限 JSON 数值，实际为 null: null
```

文件类错误示例：

```text
错误: 文件不存在: samples.csv
错误: 文件 samples.csv 不是有效的 UTF-8 编码
```

特例：文件**只有合法表头而没有数据行**时，视为正常输入，退出码 `0` 且标准输出为空。
