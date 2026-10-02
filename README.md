# 传感器数据回放实验台

建设用于教学和接口联调的本地传感器数据回放产品，逐步覆盖 CSV 或 JSONL 采样导入、通道定义、时间轴查询、按时间区间回放、暂停与续播、简单重采样、缺测提示和统计导出。

计划采用：Python 3 标准库 / csv / json / datetime / argparse。

当前版本交付最小可运行功能：**CSV / JSONL 温湿度样本的命令行即时回放**（仅依赖 Python 3 标准库，无需安装）。暂停续播、通道配置和统计导出留待后续版本。

## 运行环境

- Python 3（仅使用标准库）
- 无需创建虚拟环境或安装依赖

## 公开命令

```bash
python -m sensor_replay <文件路径> [--format csv|jsonl] [--start-ms MS] [--end-ms MS] [--gap-threshold-ms MS] [--duplicate-policy all|first|last]
```

- 文件路径是唯一必填参数，**支持带空格的路径**（用引号包裹即可）：

  ```bash
  python -m sensor_replay "my data/demo.csv"
  ```

- `--format csv|jsonl`：显式选择输入文件格式，默认 `csv`。**不按文件扩展名推断**——
  即使文件名为 `demo.jsonl`，省略该选项时仍按 CSV 解析；非法取值或缺值均为输入错误。
- `--start-ms MS` / `--end-ms MS`：可选的**时间区间回放**，只输出 `timestamp_ms`
  落在闭区间 `[start, end]` 内的原始样本（两个端点都包含，两端相等是合法区间）。
  两者可单独使用，省略的一端表示不限制；全部省略时保持全量回放。
  取值只接受非负十进制整数（允许前导零，不接受符号、小数、指数或空白）。
  区间外的非法数据不会被忽略，仍使整个输入失败。
- `--gap-threshold-ms MS`：可选的**采样间隔缺测标记**，取值只接受由 ASCII 数字
  组成且数值大于零的十进制整数（允许前导零；缺值、空字符串、零、符号、空白、
  小数、指数或其他非数字写法均为输入错误）。启用后每条输出记录在原有四个字段
  之外只增加布尔字段 `missing_before`：
  - 标记以**区间筛选后按时间戳稳定排序**的相邻记录为依据；当前记录与上一条
    **输出**记录的 `timestamp_ms` 差值**严格大于** `MS` 时为 `true`，否则为
    `false`；
  - 首条输出记录固定为 `false`；差值恰好等于阈值、重复时间戳（差值为 0）也为
    `false`；
  - 区间外的样本和区间端点不参与判定——例如区间从 1500 开始时，首条 1500 记录
    不会与区间外的 500 记录比较；
  - 缺省该参数时输出仍只含原有四个数值字段；标记不增减记录，也不改写温湿度
    数值与 `elapsed_ms`（仍从首条选中记录计起）。
- `--duplicate-policy all|first|last`：可选的**重复采样点保留策略**，默认
  `all`（省略时等同 `all`，输出与旧版本完全一致）。重复依据为**解析后的
  timestamp_ms 数值**——即使 CSV 字面量写法不同（`01500` 与 `1500`）、
  记录不相邻或温湿度不同，也归入同一时间戳组：
  - `all`：保留同一时间戳的全部样本（维持源文件先后顺序）；
  - `first`：只保留该时间戳在**源文件中最先出现**的一条完整样本；
  - `last`：只保留该时间戳在**源文件中最后出现**的一条完整样本；
  - 选中的温湿度始终来自**同一条原始记录**，不平均、不拼接；
  - 两种格式均**先按现有规则校验完整文件并完成闭区间筛选**，再在区间内
    应用保留策略；被舍弃记录或区间外记录若非法，仍使整次回放失败（退出码
    `2`，标准输出为空，标准错误保留原有定位，无堆栈）；
  - 策略不改变字段集合与 `elapsed_ms`（仍从最终首条输出的时间戳计起）；
    同时启用缺测参数时，`missing_before` 比较**去重后最终相邻的输出记录**，
    首条固定为 `false`，差值严格大于阈值才为 `true`；
  - 缺值、空字符串或 `all`/`first`/`last` 之外的取值均为输入错误（退出码
    `2`，标准输出为空，错误信息点名 `--duplicate-policy`，无堆栈）。
- 查看帮助：

  ```bash
  python -m sensor_replay --help
  ```

## 输入文件格式

两种格式均为 UTF-8 编码，**允许文件开头带 BOM**，并遵循相同的回放与错误约定。

### CSV

- 首行为表头，必须恰好包含三列：`timestamp_ms`、`temperature`、`humidity`。
  - 列顺序可以任意；
  - 不接受额外列或重复列。
- `timestamp_ms`：从某个起点开始的毫秒数，只接受**非负十进制整数**（不接受负号、小数、空白）。
- `temperature`、`humidity`：有限数值，允许小数、负数和指数写法；不施加物理范围限制；`NaN`、`Infinity` 及溢出为无穷的值一律拒绝。
- 数据行顺序任意，允许乱序和重复时间戳。

### JSONL（JSON Lines，`--format jsonl`）

- 每个**非空物理行恰好是一个 JSON 对象**；仅含空白的行被忽略（但仍计入行号）；末行可以没有换行符。
- 每个对象必须**恰好**包含 `timestamp_ms`、`temperature`、`humidity` 三个键：
  - 缺键、多键、重复键一律拒绝；键的顺序任意。
- `timestamp_ms`：只接受**不带负号的 JSON 整数字面量**（如 `1000`、`0`）；
  拒绝负数（含 `-0`）、小数（`1.0`）、指数（`1e3`）。
- `temperature`、`humidity`：接受**有限 JSON 数值**，允许负数、小数和指数写法
  （如 `-3.5`、`1e3`），不施加物理范围限制。
- 三个字段均拒绝字符串、布尔值与 `null`；`NaN`、`Infinity`、`-Infinity` 及
  浮点溢出（如 `1e999`）一律拒绝。
- 整文件先逐行校验通过，再进行区间筛选；**区间外的非法记录同样使整次回放失败**。
- 空文件、仅含空白行或区间无命中时正常退出（`0`，两个输出流均为空）。

## 回放与输出规则

- 按 `timestamp_ms` 升序回放；时间戳重复时默认（`--duplicate-policy all`）保留全部记录，并维持它们在源文件中的先后顺序（稳定排序）。选择 `first`/`last` 时，同一时间戳只保留源文件中最先/最后出现的一条完整样本（语义见上文命令说明）；重复依据为解析后的时间戳数值，记录不必相邻。
- 使用**演示时钟立即回放**：排序后第一条样本的回放时间为零，后续样本按其时间戳与首条样本的差值推进，即 `elapsed_ms = timestamp_ms - 首条时间戳`。指定区间时，零点为**选中记录**的最早时间戳，而非参数起点或被排除的样本。
- 没有样本落入区间时正常结束：标准输出为空，退出码 `0`。
- 标准输出逐行输出 JSON（JSON Lines），每行只含四个数值字段：

  ```json
  {"timestamp_ms":0,"elapsed_ms":0,"temperature":19.5,"humidity":55}
  ```

- 使用 `--gap-threshold-ms MS` 时，每行在四个数值字段之后追加一个布尔字段
  `missing_before`（语义见上文命令说明）：

  ```json
  {"timestamp_ms":4000,"elapsed_ms":2500,"temperature":23,"humidity":58,"missing_before":true}
  ```

- 忠实保留每条样本的温湿度数值，不插值、不平均；缺测标记同样不增减记录。
- 正常结束退出码为 `0`。

## 本地演示

仓库自带 CSV 样本文件 `samples.csv`，内容如下（注意输入是乱序的，且存在重复时间戳）：

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

JSONL 示例见仓库自带的 `demo.jsonl`（三行对象，乱序且含重复时间戳）：

```bash
python -m sensor_replay demo.jsonl --format jsonl --start-ms 500 --end-ms 2000
```

```json
{"timestamp_ms":1000,"elapsed_ms":0,"temperature":20.5,"humidity":60}
{"timestamp_ms":1000,"elapsed_ms":0,"temperature":21,"humidity":61}
{"timestamp_ms":2000,"elapsed_ms":1000,"temperature":22,"humidity":62}
```

缺测标记示例见仓库自带的 `gap.csv`（四行乱序数据，含重复时间戳）：

```bash
python -m sensor_replay gap.csv --gap-threshold-ms 1000
```

```json
{"timestamp_ms":500,"elapsed_ms":0,"temperature":20,"humidity":55,"missing_before":false}
{"timestamp_ms":1500,"elapsed_ms":1000,"temperature":21,"humidity":56,"missing_before":false}
{"timestamp_ms":1500,"elapsed_ms":1000,"temperature":22,"humidity":57,"missing_before":false}
{"timestamp_ms":4000,"elapsed_ms":3500,"temperature":23,"humidity":58,"missing_before":true}
```

时间戳 500→1500 的差值恰好为 1000，不算缺测；两条 1500 为重复时间戳，也不算；
1500→4000 的差值 2500 严格大于 1000，故最后一条为 `true`。再叠加区间：

```bash
python -m sensor_replay gap.csv --gap-threshold-ms 1000 --start-ms 1500 --end-ms 4000
```

```json
{"timestamp_ms":1500,"elapsed_ms":0,"temperature":21,"humidity":56,"missing_before":false}
{"timestamp_ms":1500,"elapsed_ms":0,"temperature":22,"humidity":57,"missing_before":false}
{"timestamp_ms":4000,"elapsed_ms":2500,"temperature":23,"humidity":58,"missing_before":true}
```

区间外的 500 记录被排除后不参与判定，首条选中记录固定为 `false`；相同记录用
`--format jsonl` 输入得到完全相同的结果。

重复采样点保留策略示例见仓库自带的 `demo.csv`（四行乱序数据，`1500` 出现两次且温湿度不同）：

```bash
python -m sensor_replay demo.csv --duplicate-policy first --start-ms 1500 --end-ms 4000 --gap-threshold-ms 1000
```

```json
{"timestamp_ms":1500,"elapsed_ms":0,"temperature":21,"humidity":56,"missing_before":false}
{"timestamp_ms":4000,"elapsed_ms":2500,"temperature":23,"humidity":58,"missing_before":true}
```

同一时间戳的两条 `1500` 中，`first` 保留源文件中最先出现的 `21/56`；缺测标记基于
去重后的相邻输出记录（1500→4000 差值 2500 严格大于 1000，末条为 `true`）。把
`first` 改为 `last` 后，仅首条记录的温湿度变为 `22/57`，其余完全相同：

```bash
python -m sensor_replay demo.csv --duplicate-policy last --start-ms 1500 --end-ms 4000 --gap-threshold-ms 1000
```

```json
{"timestamp_ms":1500,"elapsed_ms":0,"temperature":22,"humidity":57,"missing_before":false}
{"timestamp_ms":4000,"elapsed_ms":2500,"temperature":23,"humidity":58,"missing_before":true}
```

`elapsed_ms` 始终从最终首条输出的时间戳计起；等价 JSONL 输入显式选择
`--format jsonl` 时结果一致。

## 错误处理与退出码

以下情况均为输入错误：**标准输出保持为空**，错误信息写入**标准错误**，进程以退出码 **`2`** 结束，不输出堆栈：

- CSV：
  - 空文件；
  - 表头不合法（缺列、额外列、重复列、列名错误）；
  - 数据行列数与表头不符；
  - `timestamp_ms` 非法（负数、小数、缺失、非数字等）；
  - `temperature` / `humidity` 缺失、无法解析，或为 `NaN`、无穷值；
- JSONL：
  - JSON 语法错误，或某一行不是单个 JSON 对象；
  - 对象缺键、多键或存在重复键；
  - `timestamp_ms` 不是不带负号的整数字面量（负数、小数、指数等）；
  - 任一三个字段为字符串、布尔值或 `null`；
  - `temperature` / `humidity` 为 `NaN`、`Infinity`、`-Infinity` 或浮点溢出；
- 公共参数与文件错误：
  - `--format` 缺值或取值非法（非 `csv`/`jsonl`）；
  - `--start-ms` / `--end-ms` 缺值或格式非法（符号、小数、指数、空白、空值），或起点大于终点；
  - `--gap-threshold-ms` 缺值、取值为零（含 `00` 等前导零形式），或为符号、
    小数、指数、空白、空字符串及其他非 ASCII 数字写法；错误信息会点名
    `--gap-threshold-ms`；
  - `--duplicate-policy` 缺值、空字符串或取值不在 `all`/`first`/`last` 中；
    错误信息会点名 `--duplicate-policy`；
  - 文件不存在、是目录、无法读取，或不是有效 UTF-8 编码。

CSV 数据错误会注明所在的 **CSV 记录序号**（表头算第 1 条，其后的数据行依次为第 2、3……条）；JSONL 错误会注明从 1 开始的**物理行号**（空白行也计数），例如：

```text
错误: 第 3 条 CSV 记录: 数据行列数不符：应为 3 列，实际为 2 列
错误: 第 4 行: temperature 必须是有限 JSON 数值，不能是null
```

文件类错误示例：

```text
错误: 文件不存在: samples.csv
错误: 文件 samples.csv 不是有效的 UTF-8 编码
```

特例：CSV 文件**只有合法表头而没有数据行**，以及 JSONL 文件为空或**仅含空白行**时，均视为正常输入，退出码 `0` 且标准输出为空。
