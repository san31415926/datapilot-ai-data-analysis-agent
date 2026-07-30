# DataPilot：本地自然语言数据分析 Agent

> 当前状态：已选题，已完成计划记录，尚未实现。

## 项目定位

DataPilot 是面向中文电商经营数据的本地自然语言分析工作台。用户上传 CSV 或 XLSX 文件后，可以用中文提问，系统由本地 Ollama 模型生成结构化分析计划，再调用受控工具执行数据概览、只读 SQL、分组统计、异常检测和图表生成，最后根据真实工具结果生成分析报告。

这个项目与作品集中的 LearningHub 不同：LearningHub 重点是中文文档的 Embedding 检索、来源约束问答和笔记管理；DataPilot 重点是结构化数据分析、Agent 工具调用、SQL 安全和结果可追溯。

## 参考来源

本项目参考并改造：

- [`Shubhamsaboo/awesome-llm-apps`](https://github.com/Shubhamsaboo/awesome-llm-apps)
- 上游示例路径：`starter_ai_agents/ai_data_analysis_agent`

参考项目使用 Streamlit、Agno、OpenAI 模型、DuckDB、Pandas，实现 CSV/Excel 上传和自然语言数据分析。本项目不会直接声称复刻项目已经完成，而是按计划逐步替换为中文业务样例、本地 Ollama、结构化计划、受控工具和测试验证。

## 目标技术链路

```text
中文问题
  -> Ollama 本地模型生成 JSON 分析计划
  -> Pydantic 校验工具和参数
  -> 受控工具执行 DuckDB 只读查询或统计
  -> 记录工具结果和数据证据
  -> 本地模型生成中文报告
  -> 表格、图表和 Markdown/CSV/PNG 导出
```

## 计划中的能力

- CSV/XLSX 文件读取、编码处理、列类型识别和输入限制。
- 行列概览、字段类型、缺失值、重复值和异常值检查。
- DuckDB 临时表和只读 SQL 查询。
- 危险 SQL、多语句、未知表名和超限结果拦截。
- `qwen2.5:3b` 默认模型及其他已安装 Ollama 生成模型选择。
- 结构化 JSON 分析计划、Pydantic 校验和一次修复。
- 数据概览、分组统计、异常检测和图表工具。
- 中文分析报告、工具调用记录和结果导出。
- 单元测试、固定评估问题和失败案例记录。

## 当前暂不支持

- 云端 OpenAI API 默认调用。
- 任意 Python 代码执行。
- 任意写入型 SQL、真实企业数据库和生产数据接入。
- 多用户权限、登录、分布式部署和实时数据同步。
- 尚未实现的能力不会写入完成状态，也不会提前写入简历。

## 开发计划

完整的阶段目标、文件产物、验收标准和禁止事项记录在：

- [`DataPilot 项目严格执行计划`](https://github.com/san31415926/ai-application-portfolio-lab/blob/main/docs/datapilot-project-plan.md)
- [`技术决策记录`](https://github.com/san31415926/ai-application-portfolio-lab/blob/main/docs/decision-log.md)

开发顺序固定为：上游拆解 -> 项目骨架 -> 样例数据 -> 数据读取 -> 质量检查 -> SQL 安全 -> 工具 -> Ollama -> 结构化 Agent -> 报告和图表 -> 界面 -> 测试评估 -> 文档和面试复述。

## 运行说明

当前尚未实现，暂时不能提供可运行命令。实现阶段会补充独立依赖、Ollama 模型准备、Windows 启动命令、示例问题和测试命令。
