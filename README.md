# 传感器数据回放实验台

建设用于教学和接口联调的本地传感器数据回放产品，逐步覆盖 CSV 或 JSONL 采样导入、通道定义、时间轴查询、按时间区间回放、暂停与续播、简单重采样、缺测提示和统计导出。

计划采用：Python 3 标准库 / csv / json / datetime / argparse。

## 当前功能：CSV 温湿度样本回放

仅依赖 Python 3 标准库，无需安装额外依赖。

### 公开命令

```bash
python -m sensor_replay samples.csv
```

文件路径是唯一必填参数，支持带空格的路径（用引号包裹即可）；`-h` / `--help` 查看帮助。

### 输入格式

- UTF-8 编码，可带 BOM；首行为表头。
- 表头必须恰好包含 `timestamp_ms`、`temperature`、`humidity` 三列，列顺序可以变化，不接受额外列或重复列。
- `timestamp_ms`：从某个起点开始的毫秒数，只接受非负十进制整数。
- `temperature`、`humidity`：有限数值，允许小数和负数，不施加物理范围限制；NaN 和无穷值视为输入错误。

### 回放与输出

- 允许乱序输入，按 `timestamp_ms` 升序回放；时间戳重复时保留全部记录并维持源文件中的先后顺序。
- 演示时钟立即回放：第一条样本的回放时间为零，后续按其时间戳与首条样本的差值推进。
- 标准输出逐行输出 JSON，每行只含 `timestamp_ms`、`elapsed_ms`、`temperature`、`humidity` 四个数值字段，保留每条样本的原始数值，不插值、不平均。

### 示例

`samples.csv`（已包含在本仓库中）：

```csv
timestamp_ms,temperature,humidity
1000,20.5,60
0,19.5,55
1000,21,61
```

运行：

```bash
python -m sensor_replay samples.csv
```

预期输出（退出码 0）：

```json
{"timestamp_ms": 0, "elapsed_ms": 0, "temperature": 19.5, "humidity": 55}
{"timestamp_ms": 1000, "elapsed_ms": 1000, "temperature": 20.5, "humidity": 60}
{"timestamp_ms": 1000, "elapsed_ms": 1000, "temperature": 21, "humidity": 61}
```

### 错误处理

- 只有合法表头而没有数据：正常结束，标准输出为空，退出码 0。
- 以下情况视为输入错误：空文件、表头不合法、数据行列数不符、时间戳非法、数值缺失或无法解析、NaN 或无穷值。
- 文件不存在、无法读取或不是有效 UTF-8：同样按输入错误处理。
- 任何输入错误：标准输出保持为空，错误信息写入标准错误并以退出码 2 结束；数据记录错误注明其所在的 CSV 记录序号（表头算第一条），不输出堆栈。

### 后续计划

JSONL 导入、暂停与续播、通道配置、统计导出等功能留待后续迭代。
