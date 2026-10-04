# 传感器数据回放实验台

建设用于教学和接口联调的本地传感器数据回放产品，逐步覆盖 CSV 或 JSONL 采样导入、通道定义、时间轴查询、按时间区间回放、暂停与续播、简单重采样、缺测提示和统计导出。

计划采用：Python 3 标准库 / csv / json / datetime / argparse。

当前版本交付最小可运行功能：**CSV / JSONL 温湿度样本的命令行即时回放**（仅依赖 Python 3 标准库，无需安装）。暂停续播、通道配置和统计导出留待后续版本。

## 运行环境

- Python 3（仅使用标准库）
- 无需创建虚拟环境或安装依赖

## 公开命令

```bash
python -m sensor_replay <文件路径> [--format csv|jsonl] [--start-ms MS] [--end-ms MS] [--gap-threshold-ms MS] [--duplicate-policy all|first|last] [--min-interval-ms MS] [--summary]
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
- `--min-interval-ms MS`：可选的**最小时间间隔抽样**，取值只接受由 ASCII
  数字组成且数值大于零的十进制整数（允许前导零；缺值、空字符串、零、符号、
  空白、小数、指数或其他非数字写法均为输入错误）。抽样在**整文件校验通过后**，
  以现有闭区间筛选与重复点策略确定的记录为范围，按时间戳升序进行：
  - 最早的时间戳组**始终保留**；其后仅当某组与上一个**保留**组的
    `timestamp_ms` 差值**至少为 MS**（`>=`，恰好相等也保留）时，才整体
    保留该组；被跳过的组**不改变比较基准**——例如保留 0、跳过 900 后，
    1500 与基准 0 的差值 1500 ≥ MS 仍会保留；末组不足间隔时**不强行保留**；
  - 同一 `timestamp_ms` 的记录（`all` 下可能有多条）作为一个组**整体保留
    或整体跳过**；保留组内维持源文件先后顺序，`first`/`last` 仍只选择源
    文件中对应的那条完整记录；
  - 抽样**只选择既有完整记录**：不生成新时间戳、不插值、不平均，保留记录
    的温湿度数值不变；`elapsed_ms` 从**抽样后**的首条记录重新计起；
  - 同时启用 `--gap-threshold-ms` 时，`missing_before` 比较**抽样后**的
    相邻输出记录，首条仍固定为 `false`；
  - 同时启用 `--summary` 时，计数、首末时间戳、跨度与各通道极值均基于
    **抽样后的最终记录**，字段与空摘要约定不变；合法输入抽样后无记录时
    回放为空、摘要仍输出既有空摘要；
  - 区间外或会被重复策略、抽样舍弃的非法记录**仍使整个输入失败**，并保留
    原有错误定位（退出码 `2`，标准输出为空，无堆栈）；
  - 省略该参数时保持全部既有行为不变；CSV 与显式 `--format jsonl` 遵循
    完全相同的规则。
- `--summary`：可选的**无值统计摘要开关**。启用后不进行回放，标准输出
  **只含唯一一个 JSON 对象和末尾换行**，成功退出码为 `0` 且标准错误为空；
  省略时保持现有 JSON Lines 回放输出、缺测标记和演示时钟行为不变。摘要恰好
  含以下六个字段（键顺序任意）：
  - `sample_count`：按整文件校验后，经闭区间筛选和重复点策略确定的**最终
    记录计数**；`all` 下重复点分别计数，`first`/`last` 沿用源文件取完整
    样本的规则；
  - `first_ms` / `last_ms`：最终记录最早、最晚的 `timestamp_ms`；
  - `duration_ms` = `last_ms - first_ms`（单条记录跨度为 0；**不以参数
    边界代替**）；
  - `temperature` / `humidity`：各为**仅含 `min`、`max`** 的对象，取各通道
    最终记录的真实样本极值，不插值、不平均；单条记录时两个极值相同；
  - 合法输入**无选中记录**时仍输出摘要：`sample_count` 与 `duration_ms`
    为 `0`，`first_ms`、`last_ms` 及各通道极值为 `null`；CSV 只有合法表头、
    JSONL 为空或仅含空白行同样如此（**空 CSV 仍报错**）；
  - 可与 `--gap-threshold-ms` 同时使用：阈值继续按既有规则校验，合法取值
    不改变摘要字段和值；
  - 摘要仅基于最终记录，不包含其他字段；`--summary=任意值` 一律拒绝（退出码
    `2`，错误信息点名 `--summary`，无堆栈）。
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
- 使用 `--min-interval-ms MS` 时，在区间筛选与重复点策略确定的记录上按时间戳
  组抽样（首组始终保留，其后与上一个**保留**组差值至少为 MS 的组才整体保留，
  差值恰好相等也保留；被跳过的组不移动基准，末组不足间隔不强行保留）；抽样只
  减少输出，不生成时间戳、不改写温湿度，`elapsed_ms` 与缺测标记均以抽样后的
  记录重新计算。
- 正常结束退出码为 `0`。

### 统计摘要（`--summary`）

启用 `--summary` 时不回放记录，标准输出为**唯一一个 JSON 对象加末尾换行**
（不是 JSON Lines，没有逐行输出），退出码 `0`、标准错误为空。摘要恰好含
`sample_count`、`first_ms`、`last_ms`、`duration_ms`、`temperature`、
`humidity` 六个字段，其中 `temperature`/`humidity` 各自只含 `min`/`max`：

```bash
python -m sensor_replay samples.csv --summary
```

```json
{"sample_count":3,"first_ms":0,"last_ms":1000,"duration_ms":1000,"temperature":{"min":19.5,"max":21},"humidity":{"min":55,"max":61}}
```

计数与极值均基于**整文件校验后**再经闭区间筛选与重复点策略确定的最终记录；
`all`（默认）下重复点分别计数。使用 `first`/`last` 时极值取自策略保留下来的
完整样本，不平均、不拼接。无选中记录时输出：

```json
{"sample_count":0,"first_ms":null,"last_ms":null,"duration_ms":0,"temperature":{"min":null,"max":null},"humidity":{"min":null,"max":null}}
```

`--gap-threshold-ms` 可与 `--summary` 同时使用，但缺测标记不进入摘要；阈值
取值仍按既有规则校验。最小间隔抽样同样可与 `--summary` 同时使用：摘要统计
**抽样后**的最终记录，字段与空摘要约定不变。区间、重复策略、格式选择
（`--format jsonl`）与文件编码约定均与回放模式完全一致；等价 JSONL 显式
指定格式后摘要结果相同。

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

最小时间间隔抽样示例（六行乱序数据，`1000` 出现两次）：

```csv
timestamp_ms,temperature,humidity
1900,29,69
1000,20,60
0,10,50
700,17,57
1000,21,61
2000,30,70
```

```bash
python -m sensor_replay demo.csv --min-interval-ms 1000
```

```json
{"timestamp_ms":0,"elapsed_ms":0,"temperature":10,"humidity":50}
{"timestamp_ms":1000,"elapsed_ms":1000,"temperature":20,"humidity":60}
{"timestamp_ms":1000,"elapsed_ms":1000,"temperature":21,"humidity":61}
{"timestamp_ms":2000,"elapsed_ms":2000,"temperature":30,"humidity":70}
```

时间戳 `0` 组始终保留；`700` 与基准 `0` 的差值 700 < 1000 被跳过且不移动
基准；`1000` 组与基准 `0` 的差值恰好为 1000，整组保留（`all` 下两条都保留，
维持源文件顺序）；`1900` 与上一个保留组 `1000` 的差值 900 < 1000 被跳过；
`2000` 与 `1000` 的差值恰好为 1000，予以保留。抽样不生成记录、不改写温
湿度，`elapsed_ms` 从抽样后首条记录（0）计起；等价 JSONL 显式选择
`--format jsonl` 时结果相同。

## 错误处理与退出码

以下情况均为输入错误：**标准输出保持为空**，错误信息写入**标准错误**，进程以退出码 **`2`** 结束，不输出堆栈：

- CSV：
  - 空文件；
  - 表头不合法（缺列、额外列、重复列、列名错误）；
  - 数据行列数与表头不符；
  - `timestamp_ms` 非法（负数、小数、缺失、非数字等）；
  - `temperature` / `humidity` 缺失、无法解析，或为 `NaN`、无穷值；
  - 底层 CSV 解析错误（`csv.Error`，如字段长度超过 `csv.field_size_limit()` 上限）：错误信息含“CSV 解析失败”，按逻辑 CSV 记录序号定位——表头算第 1 条，被双引号包裹且跨越物理行的字段也只按记录序号计数；字段长度上限与引号解析规则沿用 Python 标准库 `csv` 默认行为；
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
  - `--min-interval-ms` 缺值、取值为零（含 `00` 等前导零形式），或为符号、
    小数、指数、空白、空字符串及其他非 ASCII 数字写法；错误信息会点名
    `--min-interval-ms`；
  - `--summary` 带任何取值（如 `--summary=true`、`--summary=`）；错误信息
    会点名 `--summary`；
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

特例：CSV 文件**只有合法表头而没有数据行**，以及 JSONL 文件为空或**仅含空白行**时，均视为正常输入，退出码 `0` 且标准输出为空；使用 `--summary` 时标准输出改为无选中记录的摘要 JSON（数量与跨度为 `0`，其余为 `null`）。空 CSV（连表头都没有）仍按输入错误处理，即使指定 `--summary` 也不产生摘要。
