# Single Repo Dual Body Experiment

> 文档状态：实验设计草稿。
>
> 当前实现与验收优先参考 [phase-1-experiment-roadmap.md](../phase-1-experiment-roadmap.md)、[body-runtime-runbook.md](../body-runtime-runbook.md)、[body-lifecycle.md](../body-lifecycle.md)。本文保留为“单仓库双槽位”思路来源。

## 1. 目的

本文件把 VoidCube 的“单仓库、双身体、交替进化”思路整理成一份偏实施向的实验设计。

它不是正式协议，而是连接当前仓库现实与目标架构之间的桥梁。

这里要明确采用当前基线里的解释：

- VoidCube 是母体系统
- 双身体对应的是两个子 Agent
- 母体的目标不是把自己整体暴露给用户
- 母体的目标是持续产出更好的子 Agent，再把活跃子 Agent 切给用户

## 2. 为什么坚持单仓库

单仓库的好处是：

- 身体模板统一
- 版本历史集中
- 补丁与演化记录容易追溯
- 实验成本低

对当前阶段来说，单仓库完全可行。真正要解决的不是仓库数量，而是：

- 如何从单仓库派生出两具身体
- 如何让两具身体互不污染
- 如何让切换不破坏身份连续性

## 3. 基本模型

推荐最小模型：

- 一个主仓库
- 两个身体槽位
  - `slot-A`
  - `slot-B`
- 一个共享 Mem 灵魂层
- 一个统一网关
- 一个生命周期执行器

这个模型里最关键的不是“只有两个槽位”这件事本身，而是：

- 两个槽位对应两个独立可管理的子 Agent 实例
- 母体逻辑和子 Agent 运行结构必须分开管理

## 4. 槽位不是分支，槽位是身体实例

这一点最重要：

- Git branch 不是身体
- Git worktree 也不直接等于身体
- 槽位是“工作树 + 运行目录 + 元数据 + 进程状态”的组合体

进一步说：

- 槽位也不只是目录技巧
- 槽位是母体管理子 Agent 的最小交付单元
- 如果子 Agent 不能被独立落盘、独立观察、独立切换，就很难被稳定治理

建议把槽位抽象成：

```text
BodySlot = {
  slot_id,
  worktree_path,
  runtime_path,
  state,
  version,
  pid,
  lease,
  last_probe_result,
  last_switch_at
}
```

## 5. 推荐最小目录

```text
repo-root/
  .body-slots/
    slot-A/
      worktree/
      runtime/
      logs/
      meta.json
    slot-B/
      worktree/
      runtime/
      logs/
      meta.json
  .body-registry.json
```

其中：

- `.body-registry.json`
  - 保存全局身体注册信息
- 每个槽位自己的 `meta.json`
  - 保存槽位级状态

这里建议明确把每个槽位理解成一个独立子 Agent 文件根：

- `worktree/`
  - 子 Agent 的代码与模板工作树
- `runtime/`
  - 子 Agent 的运行态目录
- `logs/`
  - 子 Agent 的运行观察与审计痕迹
- `meta.json`
  - 子 Agent 的生命周期、版本、探测与切换元数据

这样做的意义不是为了“看起来整洁”，而是为了让母体能真正管理两个孩子，而不是把它们混成一团。

## 6. 全局注册表建议

`.body-registry.json` 建议至少记录：

- `active_slot`
- `shell_slot`
- `retired_slot`
- `current_generation`
- `watch_window`
- `last_switch_result`

## 7. 与现有仓库的结合点

当前仓库已有一些很适合承接实验的基础：

- `cli.py` 已支持 git worktree
- `systems/gateway/` 已有服务路由思路
- `systems/execution/` 与相关生命周期模块已具备执行雏形
- `VOIDCUBE_HOME` 已支持独立状态根目录

所以最顺的路径不是另起炉灶，而是：

- 复用 worktree 能力作为槽位工作树
- 复用 `systems/` 作为长期演化主线
- 让 Mem 成为真正的治理中枢

同时要避免一个误区：

- 单仓库不等于单文件结构
- 单仓库之上仍然应该为两个子 Agent 维持独立槽位结构

## 8. 推荐的实验边界

第一阶段不要直接尝试：

- 多候选体并发竞争
- 多本体协同
- 热内存迁移
- 长上下文实时转移

第一阶段只做：

- 双槽位
- 单活体
- 单候选体
- 固定 `probe`
- 明确观察窗口
- 明确回滚

## 9. 最小切换实现图

```text
slot-A = active
slot-B = shell

slot-A 改造 slot-B
slot-B -> candidate
slot-B -> probe
Mem(Governor Mode) 批准
Gateway 切流到 slot-B
slot-B -> active
slot-A -> retired
观察窗口通过
slot-A -> shell
```

## 10. 风险点

实验期最主要的风险有 4 类：

- 槽位本地状态污染
- `probe` 检查不充分
- 切流与回滚协议不清晰
- 灵魂层误把短期缓存当长期身份

因此，真正需要严控的是：

- runtime 隔离
- 状态边界
- 切换记录
- 治理裁决结构化

还要再加一条：

- 子 Agent 文件结构不独立，导致母体无法精确知道“当前交付给用户的是哪一个孩子”

## 11. 近期最值得先实现的东西

如果后面开始动代码，我建议优先级是：

1. 槽位注册表
2. 固定双槽位 worktree/runtime 布局
3. 当前活跃槽位切换指针
4. `probe` 检查协议
5. 观察窗口与回滚记录

更细的执行拆分见 [phase-1-experiment-roadmap.md](../phase-1-experiment-roadmap.md)。

## 12. 结论

单仓库不是你的限制，反而是实验期优势。

你真正要做的是：

- 把仓库当身体模板
- 把槽位当身体实例
- 把 Mem 当身份与治理核心
- 把网关当接入权与流量调度器

再用母体-子体的说法总结就是：

- 仓库是母体培养子 Agent 的模板土壤
- 槽位是两个子 Agent 各自独立的成长空间
- 用户最终接触到的，只是当前被母体放到前台的那个活跃子 Agent

这样，“身体轮换”就不再是危险的活体复制，而是受协议约束的实例交替。
