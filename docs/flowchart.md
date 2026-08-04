# DataPilot 系统流程图

## 主流程

```mermaid
flowchart TD
    Start([开始]) --> Upload[上传 CSV 或 XLSX]
    Upload --> Load[读取文件并识别字段类型]
    Load --> Quality[检查缺失值、重复值和异常值]
    Quality --> Dataset[建立字段目录和当前数据表]
    Dataset --> Question[用户输入中文分析问题]
    Question --> Intent[Ollama 模型提取分析意图]
    Intent --> Plan[生成结构化分析计划]
    Plan --> PlanCheck{Pydantic 校验}
    PlanCheck -- 不通过 --> Reject[返回拒答或修改提示]
    PlanCheck -- 通过 --> ToolCheck[校验工具参数和字段白名单]
    ToolCheck --> SQLCheck{只读 SQL 校验}
    SQLCheck -- 不通过 --> Reject
    SQLCheck -- 通过 --> Execute[DuckDB 执行受控查询]
    Execute --> Evidence[整理真实数据结果和执行记录]
    Evidence --> Report[本地模型生成中文报告]
    Report --> Output[展示表格、图表和分析结论]
    Output --> Export[导出 CSV、Markdown 或 PNG]
```

## 各模块职责

| 模块 | 负责内容 |
| --- | --- |
| 数据加载 | 读取 CSV/XLSX，处理编码、表头和基础类型 |
| 数据质量 | 检查缺失、重复、唯一值、数值范围和异常记录 |
| 意图识别 | 把中文问题转换为目标、维度、指标、聚合和筛选条件 |
| 计划校验 | 使用 Pydantic 限制计划结构和字段引用 |
| 安全查询 | 只允许访问当前数据，执行单条只读 SQL |
| 工具执行 | 完成总览、分组统计、异常检测和图表配置 |
| 报告生成 | 仅根据已执行工具返回的真实结果生成中文报告 |
| 页面展示 | 展示分析过程、表格、图表和下载文件 |
