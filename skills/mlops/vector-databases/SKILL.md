---
name: vector-databases
description: Design, configure, and troubleshoot vector databases and embedding-backed retrieval systems for RAG and semantic search. Use when implementing vector storage, similarity queries, embedding pipelines, indexing, or retrieval evaluation.
---

# 向量数据库

使用向量数据库时，先确认嵌入模型、向量维度、距离度量和持久化后端，再设计索引与查询流程。保持写入和查询使用同一嵌入模型及维度。

## 工作流

1. 明确数据来源、分块策略、元数据过滤和召回数量。
2. 检查后端是否支持目标维度、距离度量和持久化。
3. 建立幂等索引写入，使用稳定文档 ID，避免重复向量。
4. 查询时先生成与写入一致的嵌入，再应用元数据过滤和 top-k 召回。
5. 用已标注查询评估召回率、延迟和上下文预算；记录模型或维度变更并重建索引。

## 验证要点

- 写入一条向量后可按 ID 读取并删除。
- 相同查询在重复执行时返回稳定排序。
- 不同维度或模型版本被明确拒绝，而不是静默混用。
- 数据库连接失败时返回可诊断错误，并保留可重试路径。
