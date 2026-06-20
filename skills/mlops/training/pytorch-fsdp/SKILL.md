---
name: pytorch-fsdp
description: 使用PyTorch FSDP进行完全分片数据并行训练的专家指导 - 参数分片、混合精度、CPU卸载、FSDP2
version: 1.0.0
author: Orchestra Research
license: MIT
dependencies: [torch>=2.0, transformers]
metadata:
  VoidCube:
    tags: [Distributed Training, PyTorch, FSDP, Data Parallel, Sharding, Mixed Precision, CPU Offloading, FSDP2, Large-Scale Training]

---

# Pytorch-Fsdp 技能

基于官方文档生成的pytorch-fsdp开发综合辅助。

## 何时使用此技能

此技能应在以下情况下触发:
- 使用pytorch-fsdp工作
- 询问pytorch-fsdp功能或API
- 实现pytorch-fsdp解决方案
- 调试pytorch-fsdp代码
- 学习pytorch-fsdp最佳实践

## 快速参考

### 常见模式

**模式1:** 通用Join上下文管理器,用于不均匀输入的分布式训练。

```
Join
```

**模式2:** 分布式通信包 - torch.distributed

```
torch.distributed
```

**模式3:** 初始化 - 包需要使用torch.distributed.init_process_group()或torch.distributed.device_mesh.init_device_mesh()函数初始化。

```
torch.distributed.init_process_group()
```

**模式4:** 示例:

```
>>> from torch.distributed.device_mesh import init_device_mesh
>>>
>>> mesh_1d = init_device_mesh("cuda", mesh_shape=(8,))
>>> mesh_2d = init_device_mesh("cuda", mesh_shape=(2, 8), mesh_dim_names=("dp", "tp"))
```

**模式5:** 组 - 默认情况下集合操作在默认组(也称为世界)上操作,并要求所有进程进入分布式函数调用。

```
new_group()
```

**模式6:** 警告:使用多个进程组与NCCL后端时的安全并发使用。

```
NCCL
```

**模式7:** 注意:如果在分布式RPC框架中使用DistributedDataParallel,应始终使用torch.distributed.autograd.backward()计算梯度。

```
torch.distributed.autograd.backward()
```

**模式8:** static_graph (bool) - 当设置为True时,DDP知道训练图是静态的。

```
True
```

## 参考文件

此技能在`references/`中包含综合文档:

- **other.md** - 其他文档

需要详细信息时使用`view`读取特定参考文件。

## 使用此技能

### 对于初学者
从getting_started或tutorials参考文件开始学习基础概念。

### 对于特定功能
使用相应的类别参考文件(api、guides等)获取详细信息。

### 对于代码示例
上面的快速参考部分包含从官方文档提取的常见模式。

## 资源

### references/
从官方来源提取的组织文档。这些文件包含:
- 详细说明
- 带语言注释的代码示例
- 原始文档链接
- 用于快速导航的目录

### scripts/
在此添加常见自动化任务的辅助脚本。

### assets/
在此添加模板、样板或示例项目。

## 注意事项

- 此技能是从官方文档自动生成的
- 参考文件保留了源文档的结构和示例
- 代码示例包含语言检测以实现更好的语法高亮
- 快速参考模式从文档中的常见用法示例提取

## 更新

要使用更新的文档刷新此技能:
1. 使用相同配置重新运行爬虫
2. 技能将使用最新信息重建

