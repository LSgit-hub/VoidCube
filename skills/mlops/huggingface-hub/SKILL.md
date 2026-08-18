---
name: huggingface-hub
description: Hugging Face Hub CLI (hf) — 搜索、下载和上传模型与数据集，管理仓库，用SQL查询数据集，部署推理端点，管理Spaces和存储桶。
version: 1.0.0
author: Hugging Face
license: MIT
metadata:
  VoidCube:
    tags: [huggingface, hf, models, datasets, hub, mlops]
---

# Hugging Face CLI (`hf`) 参考指南

`hf` 命令是与Hugging Face Hub交互的现代命令行界面，提供管理仓库、模型、数据集和Spaces的工具。

> **重要：** `hf` 命令替代了现已弃用的 `huggingface-cli` 命令。

## 快速开始
*   **安装：** `curl -LsSf https://hf.co/cli/install.sh | bash -s`
*   **帮助：** 使用 `hf --help` 查看所有可用函数和实际示例。
*   **认证：** 推荐通过 `HF_TOKEN` 环境变量或 `--token` 标志。

---

## 核心命令

### 通用操作
*   `hf download REPO_ID`：从Hub下载文件。
*   `hf upload REPO_ID`：上传文件/文件夹（推荐用于单次提交）。
*   `hf upload-large-folder REPO_ID LOCAL_PATH`：推荐用于大目录的可恢复上传。
*   `hf sync`：在本地目录和存储桶之间同步文件。
*   `hf env` / `hf version`：查看环境和版本详情。

### 认证 (`hf auth`)
*   `login` / `logout`：使用来自[huggingface.co/settings/tokens](https://huggingface.co/settings/tokens)的令牌管理会话。
*   `list` / `switch`：管理和切换多个存储的访问令牌。
*   `whoami`：识别当前登录的账户。

### 仓库管理 (`hf repos`)
*   `create` / `delete`：创建或永久删除仓库。
*   `duplicate`：将模型、数据集或Space克隆到新ID。
*   `move`：在命名空间之间转移仓库。
*   `branch` / `tag`：管理类Git引用。
*   `delete-files`：使用模式删除特定文件。

---

## 专用Hub交互

### 数据集与模型
*   **数据集：** `hf datasets list`、`info` 和 `parquet`（列出parquet URL）。
*   **SQL查询：** `hf datasets sql SQL` — 通过DuckDB对数据集parquet URL执行原始SQL。
*   **模型：** `hf models list` 和 `info`。
*   **论文：** `hf papers list` — 查看每日论文。

### 讨论与拉取请求 (`hf discussions`)
*   管理Hub贡献的生命周期：`list`、`create`、`info`、`comment`、`close`、`reopen` 和 `rename`。
*   `diff`：查看PR中的变更。
*   `merge`：完成拉取请求。

### 基础设施与计算
*   **端点：** 部署和管理推理端点（`deploy`、`pause`、`resume`、`scale-to-zero`、`catalog`）。
*   **作业：** 在HF基础设施上运行计算任务。包括 `hf jobs uv` 用于运行带内联依赖的Python脚本，以及 `stats` 用于资源监控。
*   **Spaces：** 管理交互式应用。包括 `dev-mode` 和 `hot-reload` 用于无需完整重启的Python文件。

### 存储与自动化
*   **存储桶：** 完整的类S3存储桶管理（`create`、`cp`、`mv`、`rm`、`sync`）。
*   **缓存：** 用 `list`、`prune`（移除脱离的修订）和 `verify`（校验和检查）管理本地存储。
*   **Webhook：** 通过管理Hub webhook（`create`、`watch`、`enable`/`disable`）自动化工作流。
*   **集合：** 将Hub项目组织到集合中（`add-item`、`update`、`list`）。

---

## 高级用法与提示

### 全局标志
*   `--format json`：产生机器可读输出用于自动化。
*   `-q` / `--quiet`：限制输出仅显示ID。

### 扩展与技能
*   **扩展：** 通过GitHub仓库使用 `hf extensions install REPO_ID` 扩展CLI功能。
*   **技能：** 用 `hf skills add` 管理AI助手技能。
