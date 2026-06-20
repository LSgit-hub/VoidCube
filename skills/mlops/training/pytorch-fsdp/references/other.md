# Pytorch-Fsdp - Other

**Pages:** 15

---

## 分布式数据并行#

**URL:** https://pytorch.org/docs/stable/notes/ddp.html

**Contents:**
- 分布式数据并行#
- 示例#
- 内部设计#
- 实现#
  - 进程组#
  - 分布式数据并行#
  - TorchDynamo DDP优化器#

Created 在: Jan 15, 2020 | Last Updated 在: Jan 25, 2024

 实现 的 torch.nn.并行.分布式数据并行 evolves over time. This design note 是 written based 在  状态 as 的 v1.4.

torch.nn.并行.分布式数据并行 (DDP) transparently performs 分布式数据并行 训练. This page describes how it works 和 reveals 实现 details.

Let us start 使用  simple torch.nn.并行.分布式数据并行 示例. This 示例 uses  torch.nn.Linear as  local 模型, wraps it 使用 DDP, 和 then runs one 前向传播, one 反向传播, 和  优化器步骤 在  DDP 模型. After that, 参数 在  local 模型 将 是 updated, 和 all 模型 在 different 进程 应该 是 exactly  same.

DDP works 使用 TorchDynamo. When used 使用 TorchDynamo, apply  DDP 模型 wrapper before compiling  模型, such that torchdynamo 可以 apply DDPOptimizer (图-break optimizations) based 在 DDP 桶 sizes. (See TorchDynamo DDP优化器 用于 more information.)

This section reveals how it works under  hood 的 torch.nn.并行.分布式数据并行 由 diving into details 的 every 步骤 在 one 迭代.

前置条件: DDP relies 在 c10d 进程组 用于 communications. Hence, applications 必须 create 进程组 instances before constructing DDP.

构造:  DDP constructor takes  reference 到  local 模块, 和 broadcasts state_dict() 从  进程 使用 rank 0 到 all other 进程 在  group 到 make sure that all 模型 副本 start 从  exact same 状态. Then, each DDP 进程 creates  local Reducer, which later 将 take care 的  梯度 同步 during  反向传播. 到 improve 通信 效率,  Reducer organizes 参数 梯度 into 桶, 和 reduces one 桶 at  time. 桶 size 可以 是 configured 由 setting  bucket_cap_mb argument 在 DDP constructor.  mapping 从 参数 梯度 到 桶 是 determined at  构造 time, based 在  桶 size limit 和 参数 sizes. 模型 参数 是 allocated into 桶 在 (roughly)  reverse order 的 模型.参数() 从  given 模型.  reason 用于 using  reverse order 是 because DDP expects 梯度 到 become ready during  反向传播 在 approximately that order.  figure below shows  示例. Note that,  grad0 和 grad1 是 在 bucket1, 和  other two 梯度 是 在 bucket0. 的 course, this assumption 可能 not always 是 true, 和 when that happens it 可以 hurt DDP backward speed as  Reducer cannot kick off  通信 at  earliest possible time. Besides bucketing,  Reducer also registers 自动求导 钩子 during 构造, one 钩子 per 参数. These 钩子 将 是 triggered during  反向传播 when  梯度 becomes ready.

前向传播:  DDP takes  输入 和 passes it 到  local 模型, 和 then analyzes  输出 从  local 模型 if find_unused_parameters 是 set 到 True. This mode allows running backward 在  subgraph 的  模型, 和 DDP finds out which 参数 是 involved 在  反向传播 由 traversing  自动求导 图 从  模型 输出 和 marking all unused 参数 as ready 用于 reduction. During  反向传播,  Reducer 将 only wait 用于 unready 参数, but it 将 still 归约 all 桶. Marking  参数 梯度 as ready 做 not help DDP skip 桶 as 用于 now, but it 将 prevent DDP 从 waiting 用于 absent 梯度 forever during  反向传播. Note that traversing  自动求导 图 introduces extra overheads, so applications 应该 only set find_unused_parameters 到 True when necessary.

反向传播:  backward() 函数 是 directly invoked 在  损失 张量, which 是 out 的 DDP’s control, 和 DDP uses 自动求导 钩子 registered at 构造 time 到 trigger 梯度 synchronizations. When one 梯度 becomes ready, its corresponding DDP 钩子 在 that grad accumulator 将 fire, 和 DDP 将 then mark that 参数 梯度 as ready 用于 reduction. When 梯度 在 one 桶 是 all ready,  Reducer kicks off  asynchronous 全归约 在 that 桶 到 calculate mean 的 梯度 across all 进程. When all 桶 是 ready,  Reducer 将 block waiting 用于 all 全归约 操作 到 finish. When this 是 done, averaged 梯度 是 written 到  param.grad field 的 all 参数. So after  反向传播,  grad field 在  same corresponding 参数 across different DDP 进程 应该 是  same.

优化器步骤: 从  优化器’s perspective, it 是 optimizing  local 模型. 模型 副本 在 all DDP 进程 可以 keep 在 sync because they all start 从  same 状态 和 they 有  same averaged 梯度 在 every 迭代.

DDP requires Reducer instances 在 all 进程 到 invoke 全归约 在 exactly  same order, which 是 done 由 always running 全归约 在  桶 index order instead 的 actual 桶 ready order. Mismatched 全归约 order across 进程 可以 lead 到 wrong results 或 DDP backward hang.

Below 是 pointers 到  DDP 实现 components.  stacked 图 shows  structure 的  code.

进程组.hpp: contains  abstract API 的 all 进程 group implementations.  c10d library provides 3 implementations out 的  box, namely, ProcessGroupGloo, ProcessGroupNCCL, 和 ProcessGroupMPI. 分布式数据并行 uses 进程组::广播() 到 send 模型 states 从  进程 使用 rank 0 到 others during 初始化 和 进程组::全归约() 到 sum 梯度.

Store.hpp: assists  rendezvous service 用于 进程 group instances 到 find each other.

分布式.py: 是  Python entry point 用于 DDP. It implements  初始化 steps 和  forward 函数 用于  nn.并行.分布式数据并行 模块 which call into C++ libraries. Its _sync_param 函数 performs intra-进程 参数 同步 when one DDP 进程 works 在 multiple 设备, 和 it also broadcasts 模型 buffers 从  进程 使用 rank 0 到 all other 进程.  inter-进程 参数 同步 happens 在 Reducer.cpp.

comm.h: implements  coalesced 广播 helper 函数 which 是 invoked 到 广播 模型 states during 初始化 和 synchronize 模型 buffers before  前向传播.

reducer.h: provides  core 实现 用于 梯度 同步 在  反向传播. It 有 three entry point functions:

Reducer:  constructor 是 called 在 分布式.py which registers Reducer::autograd_hook() 到 梯度 accumulators.

autograd_hook() 函数 将 是 invoked 由  自动求导 engine when  梯度 becomes ready.

prepare_for_backward() 是 called at  end 的 DDP 前向传播 在 分布式.py. It traverses  自动求导 图 到 find unused 参数 when find_unused_parameters 是 set 到 True 在 DDP constructor.

DDP’s 性能 advantage comes 从 overlapping 全归约 collectives 使用 computations during backwards. AotAutograd prevents this 重叠 when used 使用 TorchDynamo 用于 compiling  whole forward 和 whole backward 图, because 全归约 ops 是 launched 由 自动求导 钩子 _after_  whole optimized backwards 计算 finishes.

TorchDynamo’s DDPOptimizer helps 由 breaking  forward 图 at  logical boundaries 的 DDP’s 全归约 桶 during backwards. Note:  goal 是 到 break  图 during backwards, 和  simplest 实现 是 到 break  forward graphs 和 then call AotAutograd 和 compilation 在 each section. This allows DDP’s 全归约 钩子 到 fire 在-between sections 的 backwards, 和 schedule communications 到 重叠 使用 compute.

See this blog post 用于  more 在-depth explanation 和 experimental results, 或 read  docs 和 code at torch/_dynamo/optimizations/分布式.py

到 Debug DDPOptimizer, set TORCH_LOGS=’ddp_graphs’ 用于 full 图 dumps. 用于 logs without graphs, add any 的 ‘dynamo’, ‘分布式’, 或 ‘dist_ddp’ 到 TORCH_LOGS (用于 basic info about 桶 boundaries). 到 disable DDPOptimizer, set torch._dynamo.config.optimize_ddp=False. DDP 和 TorchDynamo 应该 still work correctly without DDPOptimizer, but 使用 性能 degradation.

---

## PyTorch文档#

**URL:** https://pytorch.org/docs/stable/

**Contents:**
- PyTorch文档#
- 索引和表格#

PyTorch 是  optimized 张量 library 用于 深度学习 using GPUs 和 CPUs.

Features described 在 this documentation 是 classified 由 release status:

Stable (API-Stable): These features 将 是 maintained long-term 和 there 应该 generally 是 no major 性能 limitations 或 gaps 在 documentation. We also expect 到 maintain backwards compatibility (although breaking changes 可以 happen 和 notice 将 是 given one release ahead 的 time).

Unstable (API-Unstable): Encompasses all features that 是 under active development where APIs 可能 change based 在 user feedback, requisite 性能 improvements 或 because coverage across operators 是 not yet complete.  APIs 和 性能 characteristics 的 these features 可能 change.

---

## 通用Join上下文管理器#

**URL:** https://pytorch.org/docs/stable/分布式.algorithms.join.html

**Contents:**
- 通用Join上下文管理器#

Created 在: Jun 06, 2025 | Last Updated 在: Jun 06, 2025

 通用Join上下文管理器 facilitates 分布式 训练 在 uneven inputs. This page outlines  API 的  relevant classes: Join, Joinable, 和 JoinHook. 用于  tutorial, see 分布式 训练 使用 Uneven Inputs Using  Join Context Manager.

This class defines  通用Join上下文管理器, which allows custom 钩子 到 是 called after  进程 joins.

These 钩子 应该 shadow  collective communications 的 non-joined 进程 到 prevent hanging 和 erroring 和 到 ensure algorithmic correctness. Refer 到 JoinHook 用于 details about  钩子 definition.

 context manager requires each participating Joinable 到 call  method notify_join_context() before its own per- 迭代 collective communications 到 ensure correctness.

 context manager requires that all process_group attributes 在  JoinHook objects 是  same. If there 是 multiple JoinHook objects, then  设备 的  first 是 used.  进程 group 和 设备 information 是 used 用于 checking 用于 non- joined 进程 和 用于 notifying 进程 到 throw  exception if throw_on_early_termination 是 enabled, both 的 which using  all- 归约.

joinables (List[Joinable]) –  list 的  participating Joinable s; their 钩子 是 iterated over 在  given order.

enable (bool) –  flag enabling uneven 输入 detection; setting 到 False disables  context manager’s functionality 和 应该 only 是 set when  user knows  inputs 将 not 是 uneven (default: True).

throw_on_early_termination (bool) –  flag controlling whether 到 throw  exception upon detecting uneven inputs (default: False).

Notifies  join context manager that  calling 进程 有 not yet joined.

Then, if throw_on_early_termination=True, checks if uneven inputs 有 是 detected (i.e. if one 进程 有 already joined) 和 throws  exception if so.

This method 应该 是 called 从  Joinable object before its per-迭代 collective communications. 用于 示例, this 应该 是 called at  beginning 的  前向传播 在 分布式数据并行.

Only  first Joinable object passed into  context manager performs  collective communications 在 this method, 和 用于  others, this method 是 vacuous.

joinable (Joinable) –  Joinable object calling this method.

 async work handle 用于  all-归约 meant 到 notify  context manager that  进程 有 not yet joined if joinable 是  first one passed into  context manager; None otherwise.

This defines  abstract base class 用于 joinable classes.

 joinable class (inheriting 从 Joinable) 应该 implement join_hook(), which returns  JoinHook instance, 在 addition 到 join_device() 和 join_process_group() that return 设备 和 进程 group information, respectively.

Return  设备 从 which 到 perform collective communications needed 由  join context manager.

Return  JoinHook instance 用于  given Joinable.

kwargs (dict) –  dict containing any keyword arguments 到 modify  behavior 的  join 钩子 at run time; all Joinable instances sharing  same join context manager 是 forwarded  same value 用于 kwargs.

Returns  进程 group 用于  collective communications needed 由  join context manager itself.

This defines  join 钩子, which provides two entry points 在  join context manager.

Entry points :  main 钩子, which 是 called repeatedly while there exists  non-joined 进程, 和  post-钩子, which 是 called once all 进程 有 joined.

到 implement  join 钩子 用于  通用Join上下文管理器, define  class that inherits 从 JoinHook 和 override main_hook() 和 post_hook() as appropriate.

Call this 钩子 while there exists  non-joined 进程 到 shadow collective communications 在  训练 迭代.

训练 迭代 i.e., 在 one 前向传播, 反向传播, 和 优化器步骤.

Call 钩子 after all 进程 有 joined.

It 是 passed  additional bool argument is_last_joiner, which indicates if  rank 是 one 的  last 到 join.

is_last_joiner (bool) – True if  rank 是 one 的  last 到 join; False otherwise.

---

## Experimental Object Oriented 分布式 API#

**URL:** https://pytorch.org/docs/stable/分布式._dist2.html

**Contents:**
- Experimental Object Oriented 分布式 API#

Created 在: Jul 09, 2025 | Last Updated 在: Jul 30, 2025

This 是  experimental new API 用于 PyTorch 分布式. This 是 actively 在 development 和 subject 到 change 或 deletion entirely.

This 是 intended as  proving ground 用于 more flexible 和 object oriented 分布式 APIs.

Bases: pybind11_object

 进程组 是  通信 primitive that allows 用于 collective 操作 across  group 的 进程.

This 是  base class that provides  接口 用于 all ProcessGroups. It 是 not meant 到 是 used directly, but rather extended 由 subclasses.

Bases: pybind11_object

 type 的  backend used 用于  进程 group.

abort all 操作 和 connections if supported 由  backend

allgather(self: torch._C._distributed_c10d.进程组, output_tensors: collections.abc.序列[collections.abc.序列[torch.张量]], input_tensors: collections.abc.序列[torch.张量], opts: torch._C._distributed_c10d.AllgatherOptions = <torch._C._distributed_c10d.AllgatherOptions object at 0x7f0162b6b9b0>) -> c10d::Work

Allgathers  输入 tensors 从 all 进程 across  进程 group.

See torch.分布式.all_gather() 用于 more details.

allgather(self: torch._C._distributed_c10d.进程组, output_tensors: collections.abc.序列[torch.张量], input_tensor: torch.张量, timeout: datetime.timedelta | None = None) -> c10d::Work

Allgathers  输入 tensors 从 all 进程 across  进程 group.

See torch.分布式.all_gather() 用于 more details.

Allgathers  输入 tensors 从 all 进程 across  进程 group.

See torch.分布式.all_gather() 用于 more details.

Allgathers  输入 tensors 从 all 进程 across  进程 group.

See torch.分布式.all_gather() 用于 more details.

全归约(self: torch._C._distributed_c10d.进程组, tensors: collections.abc.序列[torch.张量], opts: torch._C._distributed_c10d.AllreduceOptions = <torch._C._distributed_c10d.AllreduceOptions object at 0x7f0162745db0>) -> c10d::Work

Allreduces  provided tensors across all 进程 在  进程 group.

See torch.分布式.all_reduce() 用于 more details.

全归约(self: torch._C._distributed_c10d.进程组, tensors: collections.abc.序列[torch.张量], op: torch._C._distributed_c10d.ReduceOp = <RedOpType.SUM: 0>, timeout: datetime.timedelta | None = None) -> c10d::Work

Allreduces  provided tensors across all 进程 在  进程 group.

See torch.分布式.all_reduce() 用于 more details.

全归约(self: torch._C._distributed_c10d.进程组, 张量: torch.张量, op: torch._C._distributed_c10d.ReduceOp = <RedOpType.SUM: 0>, timeout: datetime.timedelta | None = None) -> c10d::Work

Allreduces  provided tensors across all 进程 在  进程 group.

See torch.分布式.all_reduce() 用于 more details.

Allreduces  provided tensors across all 进程 在  进程 group.

See torch.分布式.all_reduce() 用于 more details.

Alltoalls  输入 tensors 从 all 进程 across  进程 group.

See torch.分布式.all_to_all() 用于 more details.

alltoall_base(self: torch._C._distributed_c10d.进程组, 输出: torch.张量, 输入: torch.张量, output_split_sizes: collections.abc.序列[typing.SupportsInt], input_split_sizes: collections.abc.序列[typing.SupportsInt], opts: torch._C._distributed_c10d.AllToAllOptions = <torch._C._distributed_c10d.AllToAllOptions object at 0x7f0162b79d30>) -> c10d::Work

Alltoalls  输入 tensors 从 all 进程 across  进程 group.

See torch.分布式.all_to_all() 用于 more details.

alltoall_base(self: torch._C._distributed_c10d.进程组, 输出: torch.张量, 输入: torch.张量, output_split_sizes: collections.abc.序列[typing.SupportsInt], input_split_sizes: collections.abc.序列[typing.SupportsInt], timeout: datetime.timedelta | None = None) -> c10d::Work

Alltoalls  输入 tensors 从 all 进程 across  进程 group.

See torch.分布式.all_to_all() 用于 more details.

barrier(self: torch._C._distributed_c10d.进程组, opts: torch._C._distributed_c10d.BarrierOptions = <torch._C._distributed_c10d.BarrierOptions object at 0x7f0162745ab0>) -> c10d::Work

then all leave  call together.

See torch.分布式.barrier() 用于 more details.

barrier(self: torch._C._distributed_c10d.进程组, timeout: datetime.timedelta | None = None) -> c10d::Work

then all leave  call together.

See torch.分布式.barrier() 用于 more details.

广播(self: torch._C._distributed_c10d.进程组, tensors: collections.abc.序列[torch.张量], opts: torch._C._distributed_c10d.BroadcastOptions = <torch._C._distributed_c10d.BroadcastOptions object at 0x7f0162b7afb0>) -> c10d::Work

Broadcasts  张量 到 all 进程 在  进程 group.

See torch.分布式.广播() 用于 more details.

广播(self: torch._C._distributed_c10d.进程组, 张量: torch.张量, root: typing.SupportsInt, timeout: datetime.timedelta | None = None) -> c10d::Work

Broadcasts  张量 到 all 进程 在  进程 group.

See torch.分布式.广播() 用于 more details.

gather(self: torch._C._distributed_c10d.进程组, output_tensors: collections.abc.序列[collections.abc.序列[torch.张量]], input_tensors: collections.abc.序列[torch.张量], opts: torch._C._distributed_c10d.GatherOptions = <torch._C._distributed_c10d.GatherOptions object at 0x7f0162c301f0>) -> c10d::Work

Gathers  输入 tensors 从 all 进程 across  进程 group.

See torch.分布式.gather() 用于 more details.

gather(self: torch._C._distributed_c10d.进程组, output_tensors: collections.abc.序列[torch.张量], input_tensor: torch.张量, root: typing.SupportsInt, timeout: datetime.timedelta | None = None) -> c10d::Work

Gathers  输入 tensors 从 all 进程 across  进程 group.

See torch.分布式.gather() 用于 more details.

Get  store 的 this 进程 group.

Gets this 进程 group description

(Gets this 进程 group name. It’s cluster unique)

then all leave  call together.

See torch.分布式.monitored_barrier() 用于 more details.

Get  name 的 this 进程 group.

Get  rank 的 this 进程 group.

Receives  张量 从  specified rank.

See torch.分布式.recv() 用于 more details.

Receives  张量 从 any source.

See torch.分布式.recv() 用于 more details.

归约(self: torch._C._distributed_c10d.进程组, tensors: collections.abc.序列[torch.张量], opts: torch._C._distributed_c10d.ReduceOptions = <torch._C._distributed_c10d.ReduceOptions object at 0x7f0162bce3f0>) -> c10d::Work

Reduces  provided tensors across all 进程 在  进程 group.

See torch.分布式.归约() 用于 more details.

归约(self: torch._C._distributed_c10d.进程组, 张量: torch.张量, root: typing.SupportsInt, op: torch._C._distributed_c10d.ReduceOp = <RedOpType.SUM: 0>, timeout: datetime.timedelta | None = None) -> c10d::Work

Reduces  provided tensors across all 进程 在  进程 group.

See torch.分布式.归约() 用于 more details.

reduce_scatter(self: torch._C._distributed_c10d.进程组, output_tensors: collections.abc.序列[torch.张量], input_tensors: collections.abc.序列[collections.abc.序列[torch.张量]], opts: torch._C._distributed_c10d.ReduceScatterOptions = <torch._C._distributed_c10d.ReduceScatterOptions object at 0x7f0162ee5cf0>) -> c10d::Work

Reduces 和 scatters  输入 tensors 从 all 进程 across  进程 group.

See torch.分布式.reduce_scatter() 用于 more details.

reduce_scatter(self: torch._C._distributed_c10d.进程组, 输出: torch.张量, 输入: collections.abc.序列[torch.张量], op: torch._C._distributed_c10d.ReduceOp = <RedOpType.SUM: 0>, timeout: datetime.timedelta | None = None) -> c10d::Work

Reduces 和 scatters  输入 tensors 从 all 进程 across  进程 group.

See torch.分布式.reduce_scatter() 用于 more details.

Reduces 和 scatters  输入 tensors 从 all 进程 across  进程 group.

See torch.分布式.reduce_scatter() 用于 more details.

scatter(self: torch._C._distributed_c10d.进程组, output_tensors: collections.abc.序列[torch.张量], input_tensors: collections.abc.序列[collections.abc.序列[torch.张量]], opts: torch._C._distributed_c10d.ScatterOptions = <torch._C._distributed_c10d.ScatterOptions object at 0x7f0162b879f0>) -> c10d::Work

Scatters  输入 tensors 从 all 进程 across  进程 group.

See torch.分布式.scatter() 用于 more details.

scatter(self: torch._C._distributed_c10d.进程组, output_tensor: torch.张量, input_tensors: collections.abc.序列[torch.张量], root: typing.SupportsInt, timeout: datetime.timedelta | None = None) -> c10d::Work

Scatters  输入 tensors 从 all 进程 across  进程 group.

See torch.分布式.scatter() 用于 more details.

Sends  张量 到  specified rank.

See torch.分布式.send() 用于 more details.

Sets  default timeout 用于 all future 操作.

shutdown  进程 group

Get  size 的 this 进程 group.

Protocol 用于 进程 group factories.

Get  current 进程 group. Thread local method.

 current 进程 group.

Create  new 进程 group 使用  given backend 和 options. This group 是 independent 和 将 not 是 globally registered 和 thus not usable via  standard torch.分布式.* APIs.

backend (str) –  backend 到 use 用于  进程 group.

timeout (timedelta) –  timeout 用于 collective 操作.

设备 (Union[str, 设备]) –  设备 到 use 用于  进程 group.

**kwargs (object) – All remaining arguments 是 passed 到  backend constructor. See  backend specific documentation 用于 details.

Context manager 用于 进程 groups. Thread local method.

pg (进程组) –  进程 group 到 use.

Generator[None, None, None]

Register  new 进程 group backend.

name (str) –  name 的  backend.

func (ProcessGroupFactory) –  函数 到 create  进程 group.

---

## torch.分布式.fsdp.fully_shard#

**URL:** https://pytorch.org/docs/stable/分布式.fsdp.fully_shard.html

**Contents:**
- torch.分布式.fsdp.fully_shard#
- PyTorch FSDP2 (fully_shard)#

Created 在: Dec 04, 2024 | Last Updated 在: Jun 16, 2025

PyTorch FSDP2 (RFC) provides  fully sharded 数据 parallelism (FSDP) 实现 targeting performant eager-mode while using per-参数 sharding 用于 improved usability

See  Getting Started 使用 FSDP2 tutorial 用于 more information.

If you 是 currently using FSDP1, consider migrating 到 FSDP2 using our migration guide.

 user contract 用于 fully_shard(模型) 是 as follows

用于 模型 初始化, fully_shard converts 模型.参数() 从 plain torch.张量 到 DTensor 在-place.  参数 是 moved 到  appropriate 设备 according 到  设备 mesh.

Before forward 和 backward passes, pre-forward/backward 钩子 是 responsible 用于 all-gathering  参数 和 converting 模型.参数() 从 DTensor 到 plain torch.张量.

After forward 和 backward passes, post-forward/backward 钩子 free  unsharded 参数 (no 通信 needed) 和 convert 模型.参数() 从 plain torch.张量 back 到 DTensor.

用于  优化器, it 必须 是 initialized 使用  DTensor 模型.参数(), 和  优化器步骤 应该 是 performed 在 DTensor 参数.

Call 模型(输入) instead 的 模型.forward(输入) 到 trigger pre-forward 钩子 到 all-gather 参数. 到 make 模型.forward(输入) work, users 必须 either call 模型.unshard() explicitly 或 use register_fsdp_forward_method(模型, "forward") 到 register  forward method 用于 hooking.

fully_shard groups 参数 together 用于  single all-gather. User 应该 apply fully_shard 在  bottom-up manner. 用于 示例, 在  变换器 模型, fully_shard 应该 是 applied 到 each 层 before applying it 到  root 模型. When applied 到  root 模型, fully_shard excludes 模型.参数() 从 each 层 和 groups  remaining 参数 (e.g., embeddings, 输出 projection) into  single all-gather group.

type(模型) 是 “unioned” 使用 FSDPModule 在-place. 用于 示例, if 模型 是 originally 的 type nn.Linear, then fully_shard changes type(模型) 从 nn.Linear 到 FSDPLinear 在-place. FSDPLinear 是  instance 的 both nn.Linear 和 FSDPModule. It retains all methods 的 nn.Linear while also exposing FSDP2-specific APIs under FSDPModule, such as reshard() 和 unshard().

Fully Qualified Names (FQNs) 用于 参数 remain unchanged. If we call 模型.state_dict(),  FQNs 是  same before 和 after applying fully_shard. This 是 because fully_shard 做 not wrap  模块 but only registers 钩子 到  original 模块.

Compared 到 PyTorch FSDP1 (FullyShardedDataParallel):

FSDP2 uses DTensor-based dim-0 per-参数 sharding 用于  simpler sharding representation compared 到 FSDP1’s flat-参数 sharding, while preserving similar throughput 性能. More specifically, FSDP2 chunks each 参数 在 dim-0 across  数据 并行 workers (using torch.chunk(dim=0)), whereas FSDP1 flattens, concatenates, 和 chunks  group 的 tensors together, making reasoning about what 数据 是 present 在 each worker 和 resharding 到 different parallelisms complex. Per-参数 sharding provides  more intuitive user experience, relaxes constraints around frozen 参数, 和 allows 用于 通信-free (sharded) 状态 dicts, which otherwise require all-gathers 在 FSDP1.

FSDP2 implements  different 内存 management approach 到 handle  multi-stream usages that avoids torch.张量.record_stream. This ensures deterministic 和 expected 内存 usage 和 做 not require blocking  CPU like 在 FSDP1’s limit_all_gathers=True.

FSDP2 exposes APIs 用于 manual control over prefetching 和 collective scheduling, allowing power users more customization. See  methods 在 FSDPModule below 用于 details.

FSDP2 simplifies some 的  API surface: e.g. FSDP2 做 not directly support full 状态 dicts. Instead, users 可以 reshard  sharded 状态 dicts containing DTensor s 到 full 状态 dicts themselves using DTensor APIs like DTensor.full_tensor() 或 由 using higher-level APIs like PyTorch 分布式 Checkpoint ‘s 分布式 状态 dict APIs. Also, some other args 有 是 removed; see here 用于 details.

 frontend API 是 fully_shard that 可以 是 called 在  模块:

Apply fully sharded 数据 parallelism (FSDP) 到 模块, where FSDP shards 模块 参数, 梯度, 和 优化器 states across 数据 并行 workers 到 save 内存 at  cost 的 通信.

At 初始化, FSDP shards  模块’s 参数 across  数据 并行 workers given 由 mesh. Before forward, FSDP all-gathers  sharded 参数 across  数据-并行 workers 到 get  unsharded 参数 用于 forward 计算. If reshard_after_forward 是 True, then FSDP frees  unsharded 参数 after forward 和 re-all-gathers them 在 backward before 梯度 计算. After 梯度 计算, FSDP frees  unsharded 参数 和 归约-scatters  unsharded 梯度 across 数据-并行 workers.

This 实现 represents  sharded 参数 as DTensor s sharded 在 dim-0, while  unsharded 参数 将 是 like  original 参数 在 模块 (e.g. torch.张量 if originally torch.张量).  模块 forward pre-钩子 在 模块 all-gathers  参数, 和  模块 forward 钩子 在 模块 frees them (if needed). Similar backward 钩子 all-gather 参数 和 later free 参数 和 归约-scatter 梯度.

Since grouping multiple tensors together 用于 one collective 是 critical 用于 通信 效率, this 实现 makes this grouping first class. Calling fully_shard() 在 模块 constructs one group that includes  参数 在 模块.参数() except those already assigned 到  group 从  earlier call 在  submodule. This means that fully_shard() 应该 是 called bottom-up 在 your 模型. Each group’s 参数 是 all-gathered 在 one collective, 和 its 梯度 是 归约-scattered 在 one collective. Partitioning  模型 into multiple groups (“层 由 层”) allows 用于 peak 内存 savings 和 通信/计算 重叠. Users generally 应该 not call fully_shard() only 在  topmost root 模块.

模块 (Union[nn.模块, List[nn.模块]) –  模块 或 modules 到 shard 使用 FSDP 和 group together 用于 通信.

mesh (Optional[DeviceMesh]) – This 数据 并行 mesh defines  sharding 和 设备. If 1D, then 参数 是 fully sharded across  1D mesh (FSDP) 使用 (Shard(0),) placement. If 2D, then 参数 是 sharded across  1st dim 和 replicated across  0th dim (HSDP) 使用 (Replicate(), Shard(0)) placement.  mesh’s 设备 type gives  设备 type used 用于 通信; if  CUDA 或 CUDA-like 设备 type, then we use  current 设备.

reshard_after_forward (Optional[Union[bool, int]]) – This controls  参数 behavior after forward 和 可以 trade off 内存 和 通信: If True, then this reshards 参数 after forward 和 re-all-gathers 在 backward. If False, then this keeps  unsharded 参数 在 内存 after forward 和 avoids  all-gather 在 backward. 用于 best 性能, we usually set False 用于  root 模块, because  root 模块 是 typically required immediately when  反向传播 begins. If None, it 是 set 到 True 用于 non-root modules 和 False 用于 root modules. If  int, then this represents  world size 到 reshard 到 after forward. It 应该 是  non-trivial divisor 的  mesh shard dim size (i.e. excluding 1 和  dim size itself).  choice 可能 是  intra-node size (e.g. torch.cuda.device_count()). This allows  all-gather 在 backward 到 是 over  smaller world size at  cost 的 higher 内存 usage than setting 到 True. After forward,  参数 registered 到  模块 depend 在 到 this:  registered 参数 是  sharded 参数 if True; unsharded 参数 if False; 和  参数 resharded 到  smaller mesh otherwise. 到 modify  参数 between forward 和 backward,  registered 参数 必须 是  sharded 参数. 用于 False 或  int, this 可以 是 done 由 manually resharding via reshard().

This controls  参数 behavior after forward 和 可以 trade off 内存 和 通信:

If True, then this reshards 参数 after forward 和 re-all-gathers 在 backward.

If False, then this keeps  unsharded 参数 在 内存 after forward 和 avoids  all-gather 在 backward. 用于 best 性能, we usually set False 用于  root 模块, because  root 模块 是 typically required immediately when  反向传播 begins.

If None, it 是 set 到 True 用于 non-root modules 和 False 用于 root modules.

If  int, then this represents  world size 到 reshard 到 after forward. It 应该 是  non-trivial divisor 的  mesh shard dim size (i.e. excluding 1 和  dim size itself).  choice 可能 是  intra-node size (e.g. torch.cuda.device_count()). This allows  all-gather 在 backward 到 是 over  smaller world size at  cost 的 higher 内存 usage than setting 到 True.

After forward,  参数 registered 到  模块 depend 在 到 this:  registered 参数 是  sharded 参数 if True; unsharded 参数 if False; 和  参数 resharded 到  smaller mesh otherwise. 到 modify  参数 between forward 和 backward,  registered 参数 必须 是  sharded 参数. 用于 False 或  int, this 可以 是 done 由 manually resharding via reshard().

shard_placement_fn (Optional[Callable[[nn.参数], Optional[Shard]]]) – This callable 可以 是 used 到 override  sharding placement 用于  参数 到 shard  参数 在  dimension other than dim-0. If this callable returns  Shard placement (not None), then FSDP 将 shard according 到 that placement (e.g. Shard(1)). If sharding 在  nonzero dim, we currently require even sharding, i.e.  张量 dim size 在 that dim 必须 是 divisible 由  FSDP shard mesh size.

mp_policy (MixedPrecisionPolicy) – This controls  mixed precision policy, which offers 参数/reduction mixed precision 用于 this 模块. See MixedPrecisionPolicy 用于 details.

offload_policy (OffloadPolicy) – This controls  offloading policy, which offers 参数/梯度/优化器 状态 offloading. See OffloadPolicy 和 its subclasses 用于 details.

ignored_params (Optional[set[nn.参数]]) – Optional(Set[nn.参数]):  set 的 参数 到 是 ignored 由 FSDP. They 将 not 是 sharded, nor moved 到  设备 during init, nor 有 their 梯度 reduced 在 backward.

 模块 使用 FSDP applied (在-place).

Reshards  模块’s 参数, freeing  unsharded 参数 if they 是 allocated 和 registering  sharded 参数 到  模块. This method 是 not recursive.

钩子 (Callable[[torch.张量], None]) – User-defined all-归约 钩子 使用 expected signature 钩子(reduce_output: torch.张量) -> None where reduce_output 是  归约-scatter 输出 if only using FSDP 或  all-归约 输出 if using native HSDP.

stream (Optional[torch.cuda.Stream]) – Stream 到 run  all-归约 钩子 在. This 应该 only 是 set if not using native HSDP. If using native HSDP,  钩子 将 run 在  internally defined all-归约 stream used 由  native HSDP all-归约.

Sets whether  temporary staging buffers used 到 send 和 receive 数据 over collective communications 应该 是 allocated using  custom optimized allocator provided 由  进程组 itself (if any). This 可能 allow  进程组 到 是 more efficient. 用于 示例, when using NCCL, this enables it 到 leverage zero-copy transfers over SHARP (用于 NVLink 和/或 InfiniBand).

This cannot 是 used together 使用 set_custom_all_gather() 或 set_custom_reduce_scatter() as those APIs allow 用于 finer-grained control over each 通信, 和 this method cannot determine their staging buffer allocation strategy.

enable (bool) – Whether 到 turn 在 进程组 allocation.

Overrides  default all_gather 通信 behavior, 到 有 better control over  通信 和 内存 usage. See Comm 和 ReduceScatter 用于 details.

comm (AllGather) – Custom all-gather 通信.

Overrides  default reduce_scatter 通信 behavior, 到 有 better control over  通信 和 内存 usage. See Comm 和 ReduceScatter 用于 details.

comm (ReduceScatter) – Custom reduce_scatter 通信.

Sets whether 到 require  low-level collective 通信 primitives 到 exclusively use “sum”-type reductions, even if it comes at  cost 的 separate additional pre- 或 post-scaling 操作. This 是 needed 用于 示例 because NCCL currently supports zero-copy transfers only 用于 this kind 的 collectives.

NB: 用于 MTIA 设备, this 是 always implicitly enabled.

NB: if set_all_reduce_hook 是 used under FSDP setup,  caller needs 到 ensure  custom all-归约 across FSDP units follow this strategy as well, as FSDP 可以 no longer automatically handle that.

enable (bool) – Whether 到 only ever use ReduceOp.SUM 用于 comms.

Sets  custom divide factor 用于  梯度 reduction. This 可能 use  custom 归约 op using NCCL’s PreMulSum, which allows multiplying 由  factor before reduction.

factor (float) – Custom divide factor.

Sets whether  next backward 是  last one. 在  last backward, FSDP waits 在 pending 梯度 reduction 和 clears internal 数据 数据 structures 用于 backward prefetching. This 可以 是 useful 用于 microbatching.

Sets  FSDP modules 用于 which this FSDP 模块 应该 explicitly prefetch all-gathers 在 backward. This overrides  default backward pretching 实现 that prefetches  next FSDP 模块 based 在  reverse post-forward order.

Passing  singleton list containing  previous FSDP 模块 gives  same all-gather 重叠 behavior as  default 重叠 behavior. Passing  list 使用 at least length two 是 required 用于 more aggressive 重叠 和 将 use more reserved 内存.

modules (List[FSDPModule]) – FSDP modules 到 prefetch.

Sets  FSDP modules 用于 which this FSDP 模块 应该 explicitly prefetch all-gathers 在 forward.  prefetching runs after this 模块’s all-gather copy-out.

Passing  singleton list containing  next FSDP 模块 gives  same all-gather 重叠 behavior as  default 重叠 behavior, except  prefetched all-gather 是 issued earlier 从  CPU. Passing  list 使用 at least length two 是 required 用于 more aggressive 重叠 和 将 use more reserved 内存.

modules (List[FSDPModule]) – FSDP modules 到 prefetch.

Sets  post-优化器-步骤 event 用于  root FSDP 模块 到 wait  all-gather streams 在.

由 default,  root FSDP 模块 waits  all-gather streams 在  current stream 到 ensure that  优化器步骤 有 finished before all-gathering. However, this 可能 introduce false dependencies if there 是 unrelated 计算 after  优化器步骤. This API allows  user 到 provide their own event 到 wait 在. After  root waits 在  event,  event 是 discarded, so this API 应该 是 called 使用  new event each 迭代.

event (torch.Event) – Event recorded after  优化器步骤 到 wait all-gather streams 在.

Use set_gradient_divide_factor() instead

Sets if  模块 应该 all-归约 梯度. This 可以 是 used 到 implement 梯度 accumulation 使用 only 归约-scatter but not all-归约 用于 HSDP.

Sets if  模块 应该 sync 梯度. This 可以 是 used 到 implement 梯度 accumulation without 通信. 用于 HSDP, this controls both 归约-scatter 和 all-归约 together. This 是  equivalence 的 no_sync 在 FSDP1.

requires_gradient_sync (bool) – Whether 到 归约 梯度 用于  模块’s 参数.

recurse (bool) – Whether 到 set 用于 all FSDP submodules 或 just  passed-在 模块.

Sets if  模块 应该 reshard 参数 after backward. This 可以 是 used during 梯度 accumulation 到 trade off higher 内存 用于 reduced 通信 since  unsharded 参数 做 not need 到 是 re-all-gathered before  next forward.

reshard_after_backward (bool) – Whether 到 reshard 参数 after backward.

recurse (bool) – Whether 到 set 用于 all FSDP submodules 或 just  passed-在 模块.

Sets if  模块 应该 reshard 参数 after forward. This 可以 是 used 到 change  reshard_after_forward FSDP arg at runtime. 用于 示例, this 可以 是 used 到 set  FSDP root 模块’s value 到 True (since it 是 otherwise specially set 到 False), 或 it 可以 set  FSDP 模块’s value 到 False 用于 running evals 和 set back 到 True 用于 训练.

reshard_after_forward (bool) – Whether 到 reshard 参数 after forward.

recurse (bool) – Whether 到 set 用于 all FSDP submodules 或 just  passed-在 模块.

Sets whether  FSDP 模块’s 参数 need 到 是 unsharded 在 backward. This 可以 是 used 在 expert cases when  user knows that all 参数 在 this FSDP 模块’s 参数 group 是 not needed 用于 backward 计算 (e.g. 嵌入).

Unshards  模块’s 参数 由 allocating 内存 和 all-gathering  参数. This method 是 not recursive.  unshard follows  MixedPrecisionPolicy, so it 将 all-gather following param_dtype if set.

async_op (bool) – If True, then returns  UnshardHandle that 有  wait() method 到 wait 在  unshard op. If False, then returns None 和 waits 在  handle inside this 函数.

Optional[UnshardHandle]

If async_op=True, then FSDP 将 wait 在  pending unshard 在  模块’s pre-forward 用于  user.  user only needs 到 call wait() explicitly if  wait 应该 happen before pre-forward.

 handle 到 wait 在  FSDPModule.unshard() op.

Waits 在  unshard op. This ensures that  current stream 可以 use  unsharded 参数, which 是 now registered 到  模块.

Registers  method 在 模块 到 是 considered  forward method 用于 FSDP.

FSDP all-gathers 参数 pre-forward 和 optionally frees 参数 post-forward (depending 在 reshard_after_forward). FSDP only knows 到 做 this 用于 nn.模块.forward() 由 default. This 函数 patches  user-specified method 到 run  pre/post-forward 钩子 before/after  method, respectively. If 模块 是 not  FSDPModule, then this 是  no-op.

模块 (nn.模块) – 模块 到 register  forward method 在.

method_name (str) – Name 的  forward method.

This configures FSDP’s mixed precision. Unlike autocast, this applies mixed precision at  模块 level, not op level, which means low-precision activations 是 saved 用于 backward 和 high-到-low-precision casts 是 incurred only at 模块 boundaries.

FSDP works well 使用 模块-level mixed precision since it keeps  high-precision sharded 参数 在 内存 anyway. 在 other words, FSDP 做 not require any extra 内存 到 keep  high-precision copy 的  参数 用于  优化器步骤.

param_dtype (Optional[torch.dtype]) – This specifies  dtype 用于  unsharded 参数 和 hence  dtype 用于 forward/backward 计算 和  参数 all-gather. If this 是 None, then  unsharded 参数 uses  original dtype.  优化器步骤 uses  sharded 参数 在  original dtype. (Default: None)

reduce_dtype (Optional[torch.dtype]) – This specifies  dtype 用于 梯度 reduction (i.e. 归约-scatter 或 all-归约). If this 是 None but param_dtype 是 not None, then  reduction uses  compute dtype. This 可以 是 used 到 run 梯度 reduction 在 full precision while using low precision 用于 compute. If also 梯度 reduction 是 disabled via set_requires_gradient_sync(), then FSDP 将 accumulate 梯度 using reduce_dtype. (Default: None)

output_dtype (Optional[torch.dtype]) – This specifies  dtype 用于 casting floating-point forward outputs. This 可以 是 used 到 help implement cases where different modules 有 different mixed precision policies. (Default: None)

cast_forward_inputs (bool) – This specifies whether FSDP 应该 cast  forward’s floating-point 输入 tensors 到 param_dtype 或 not.

This base class represents  policy 的 no offloading 和 是 only used as  default value 用于  offload_policy arg.

This offload policy offloads 参数, 梯度, 和 优化器 states 到 CPU. Sharded 参数 是 copied host-到-设备 before all-gather.  all-gathered 参数 是 freed according 到 reshard_after_forward. Sharded 梯度 是 copied 设备-到-host 在 backward, 和  优化器步骤 runs 在 CPU 使用 CPU 优化器 states.

pin_memory (bool) – Whether 到 pin sharded 参数 和 梯度 内存. Pinning 内存 allows both more efficient H2D/D2H copies 和 用于  copies 到 重叠 使用 compute. However,  pinned 内存 cannot 是 used 由 other 进程. Set this 到 False if you 有 insufficient CPU 内存. (Default: True)

---

## 分布式 通信 package - torch.分布式#

**URL:** https://pytorch.org/docs/stable/分布式.html

**Contents:**
- 分布式 通信 package - torch.分布式#
- Backends#
  - Backends that come 使用 PyTorch#
  - Which backend 到 use?#
  - Common environment variables#
    - Choosing  网络 接口 到 use#
    - Other NCCL environment variables#
- Basics#
- 初始化#
  - TCP 初始化#

Created 在: Jul 12, 2017 | Last Updated 在: Sep 04, 2025

Please refer 到 PyTorch 分布式 Overview 用于  brief introduction 到 all features related 到 分布式 训练.

torch.分布式 supports four built-在 backends, each 使用 different capabilities.  table below shows which functions 是 available 用于 use 使用  CPU 或 GPU 用于 each backend. 用于 NCCL, GPU refers 到 CUDA GPU while 用于 XCCL 到 XPU GPU.

MPI supports CUDA only if  实现 used 到 build PyTorch supports it.

PyTorch 分布式 package supports Linux (stable), MacOS (stable), 和 Windows (prototype). 由 default 用于 Linux,  Gloo 和 NCCL backends 是 built 和 included 在 PyTorch 分布式 (NCCL only when building 使用 CUDA). MPI 是  optional backend that 可以 only 是 included if you build PyTorch 从 source. (e.g. building PyTorch 在  host that 有 MPI installed.)

As 的 PyTorch v1.8, Windows supports all collective communications backend but NCCL, If  init_method argument 的 init_process_group() points 到  file it 必须 adhere 到  following schema:

Local file system, init_method="file:///d:/tmp/some_file"

Shared file system, init_method="file://////{machine_name}/{share_folder_name}/some_file"

Same as 在 Linux platform, you 可以 enable TcpStore 由 setting environment variables, MASTER_ADDR 和 MASTER_PORT.

在  past, we 是 often asked: “which backend 应该 I use?”.

Use  NCCL backend 用于 分布式 训练 使用 CUDA GPU.

Use  XCCL backend 用于 分布式 训练 使用 XPU GPU.

Use  Gloo backend 用于 分布式 训练 使用 CPU.

GPU hosts 使用 InfiniBand interconnect

Use NCCL, since it’s  only backend that currently supports InfiniBand 和 GPUDirect.

GPU hosts 使用 Ethernet interconnect

Use NCCL, since it currently provides  best 分布式 GPU 训练 性能, especially 用于 multiprocess single-node 或 multi-node 分布式 训练. If you encounter any problem 使用 NCCL, use Gloo as  fallback option. (Note that Gloo currently runs slower than NCCL 用于 GPUs.)

CPU hosts 使用 InfiniBand interconnect

If your InfiniBand 有 enabled IP over IB, use Gloo, otherwise, use MPI instead. We 是 planning 在 adding InfiniBand support 用于 Gloo 在  upcoming releases.

CPU hosts 使用 Ethernet interconnect

Use Gloo, unless you 有 specific reasons 到 use MPI.

由 default, both  NCCL 和 Gloo backends 将 try 到 find  right 网络 接口 到 use. If  automatically detected 接口 是 not correct, you 可以 override it using  following environment variables (applicable 到  respective backend):

NCCL_SOCKET_IFNAME, 用于 示例 export NCCL_SOCKET_IFNAME=eth0

GLOO_SOCKET_IFNAME, 用于 示例 export GLOO_SOCKET_IFNAME=eth0

If you’re using  Gloo backend, you 可以 specify multiple interfaces 由 separating them 由  comma, like this: export GLOO_SOCKET_IFNAME=eth0,eth1,eth2,eth3.  backend 将 dispatch 操作 在  round-robin fashion across these interfaces. It 是 imperative that all 进程 specify  same number 的 interfaces 在 this variable.

Debugging - 在 case 的 NCCL failure, you 可以 set NCCL_DEBUG=INFO 到 print  explicit warning message as well as basic NCCL 初始化 information.

You 可能 also use NCCL_DEBUG_SUBSYS 到 get more details about  specific aspect 的 NCCL. 用于 示例, NCCL_DEBUG_SUBSYS=COLL 将 print logs 的 collective calls, which 可能 是 helpful when debugging hangs, especially those caused 由 collective type 或 message size mismatch. 在 case 的 topology detection failure, it 将 是 helpful 到 set NCCL_DEBUG_SUBSYS=图 到 inspect  detailed detection result 和 save as reference if further help 从 NCCL team 是 needed.

性能 tuning - NCCL performs automatic tuning based 在 its topology detection 到 save users’ tuning effort. 在 some socket-based systems, users 可能 still try tuning NCCL_SOCKET_NTHREADS 和 NCCL_NSOCKS_PERTHREAD 到 increase socket 网络 bandwidth. These two environment variables 有 是 pre-tuned 由 NCCL 用于 some cloud providers, such as AWS 或 GCP.

用于  full list 的 NCCL environment variables, please refer 到 NVIDIA NCCL’s official documentation

You 可以 tune NCCL communicators even further using torch.分布式.ProcessGroupNCCL.NCCLConfig 和 torch.分布式.ProcessGroupNCCL.Options. Learn more about them using help (e.g. help(torch.分布式.ProcessGroupNCCL.NCCLConfig)) 在  interpreter.

 torch.分布式 package provides PyTorch support 和 通信 primitives 用于 multiprocess parallelism across several 计算 nodes running 在 one 或 more machines.  class torch.nn.并行.分布式数据并行() builds 在 this functionality 到 provide synchronous 分布式 训练 as  wrapper around any PyTorch 模型. This differs 从  kinds 的 parallelism provided 由 Multiprocessing package - torch.multiprocessing 和 torch.nn.DataParallel() 在 that it supports multiple 网络-connected machines 和 在 that  user 必须 explicitly launch  separate copy 的  main 训练 script 用于 each 进程.

在  single-machine synchronous case, torch.分布式 或  torch.nn.并行.分布式数据并行() wrapper 可能 still 有 advantages over other approaches 到 数据-parallelism, including torch.nn.DataParallel():

Each 进程 maintains its own 优化器 和 performs  complete 优化 步骤 使用 each 迭代. While this 可能 appear redundant, since  梯度 有 already 是 gathered together 和 averaged across 进程 和 是 thus  same 用于 every 进程, this means that no 参数 广播 步骤 是 needed, reducing time spent transferring tensors between nodes.

Each 进程 contains  independent Python interpreter, eliminating  extra interpreter overhead 和 “GIL-thrashing” that comes 从 driving several execution threads, 模型 副本, 或 GPUs 从  single Python 进程. This 是 especially important 用于 模型 that make heavy use 的  Python runtime, including 模型 使用 循环 层 或 many small components.

 package needs 到 是 initialized using  torch.分布式.init_process_group() 或 torch.分布式.device_mesh.init_device_mesh() 函数 before calling any other methods. Both block until all 进程 有 joined.

初始化 是 not thread-safe. 进程 group creation 应该 是 performed 从  single thread, 到 prevent inconsistent ‘UUID’ assignment across ranks, 和 到 prevent races during 初始化 that 可以 lead 到 hangs.

Return True if  分布式 package 是 available.

Otherwise, torch.分布式 做 not expose any other APIs. Currently, torch.分布式 是 available 在 Linux, MacOS 和 Windows. Set USE_DISTRIBUTED=1 到 enable it when building PyTorch 从 source. Currently,  default value 是 USE_DISTRIBUTED=1 用于 Linux 和 Windows, USE_DISTRIBUTED=0 用于 MacOS.

Initialize  default 分布式 进程 group.

This 将 also initialize  分布式 package.

Specify store, rank, 和 world_size explicitly.

Specify init_method ( URL string) which indicates where/how 到 discover peers. Optionally specify rank 和 world_size, 或 encode all required 参数 在  URL 和 omit them.

If neither 是 specified, init_method 是 assumed 到 是 “env://”.

backend (str 或 Backend, optional) –  backend 到 use. Depending 在 build-time configurations, valid values include mpi, gloo, nccl, ucc, xccl 或 one that 是 registered 由  third-party plugin. Since 2.6, if backend 是 not provided, c10d 将 use  backend registered 用于  设备 type indicated 由  device_id kwarg (if provided).  known default registrations today 是: nccl 用于 cuda, gloo 用于 CPU, xccl 用于 xpu. If neither backend nor device_id 是 provided, c10d 将 detect  accelerator 在  run-time machine 和 use  backend registered 用于 that detected accelerator (或 CPU). This field 可以 是 given as  lowercase string (e.g., "gloo"), which 可以 also 是 accessed via Backend attributes (e.g., Backend.GLOO). If using multiple 进程 per machine 使用 nccl backend, each 进程 必须 有 exclusive access 到 every GPU it uses, as sharing GPUs between 进程 可以 result 在 deadlock 或 NCCL invalid usage. ucc backend 是 experimental. Default backend 用于  设备 可以 是 queried 使用 get_default_backend_for_device().

init_method (str, optional) – URL specifying how 到 initialize  进程 group. Default 是 “env://” if no init_method 或 store 是 specified. Mutually exclusive 使用 store.

world_size (int, optional) – Number 的 进程 participating 在  job. Required if store 是 specified.

rank (int, optional) – Rank 的  current 进程 (it 应该 是  number between 0 和 world_size-1). Required if store 是 specified.

store (Store, optional) – Key/value store accessible 到 all workers, used 到 exchange connection/address information. Mutually exclusive 使用 init_method.

timeout (timedelta, optional) – Timeout 用于 操作 executed against  进程 group. Default value 是 10 minutes 用于 NCCL 和 30 minutes 用于 other backends. This 是  duration after which collectives 将 是 aborted asynchronously 和  进程 将 crash. This 是 done since CUDA execution 是 async 和 it 是 no longer safe 到 continue executing user code since failed async NCCL 操作 可能 result 在 subsequent CUDA 操作 running 在 corrupted 数据. When TORCH_NCCL_BLOCKING_WAIT 是 set,  进程 将 block 和 wait 用于 this timeout.

group_name (str, optional, deprecated) – Group name. This argument 是 ignored

pg_options (ProcessGroupOptions, optional) – 进程 group options specifying what additional options need 到 是 passed 在 during  构造 的 specific 进程 groups. As 的 now,  only options we support 是 ProcessGroupNCCL.Options 用于  nccl backend, is_high_priority_stream 可以 是 specified so that  nccl backend 可以 pick up high priority cuda streams when there’re compute kernels waiting. 用于 other available options 到 config nccl, See https://docs.nvidia.com/deeplearning/nccl/user-guide/docs/API/types.html#ncclconfig-t

device_id (torch.设备 | int, optional) –  single, specific 设备 this 进程 将 work 在, allowing 用于 backend-specific optimizations. Currently this 有 two effects, only under NCCL:  communicator 是 immediately formed (calling ncclCommInit* immediately rather than  normal lazy call) 和 sub-groups 将 use ncclCommSplit when possible 到 avoid unnecessary overhead 的 group creation. If you want 到 know NCCL 初始化 error early, you 可以 also use this field. If  int 是 provided,  API assumes that  accelerator type at compile time 将 是 used.

到 enable backend == Backend.MPI, PyTorch needs 到 是 built 从 source 在  system that supports MPI.

Support 用于 multiple backends 是 experimental. Currently when no backend 是 specified, both gloo 和 nccl backends 将 是 created.  gloo backend 将 是 used 用于 collectives 使用 CPU tensors 和  nccl backend 将 是 used 用于 collectives 使用 CUDA tensors.  custom backend 可以 是 specified 由 passing 在  string 使用 format “<device_type>:<backend_name>,<device_type>:<backend_name>”, e.g. “CPU:gloo,cuda:custom_backend”.

Initializes  DeviceMesh based 在 device_type, mesh_shape, 和 mesh_dim_names 参数.

This creates  DeviceMesh 使用  n-dimensional array layout, where n 是  length 的 mesh_shape. If mesh_dim_names 是 provided, each dimension 是 labeled as mesh_dim_names[i].

init_device_mesh follows SPMD programming 模型, meaning  same PyTorch Python program runs 在 all 进程/ranks 在  cluster. Ensure mesh_shape ( dimensions 的  nD array describing 设备 layout) 是 identical across all ranks. Inconsistent mesh_shape 可能 lead 到 hanging.

If no 进程 group 是 found, init_device_mesh 将 initialize 分布式 进程 group/groups required 用于 分布式 communications behind  scene.

device_type (str) –  设备 type 的  mesh. Currently supports: “CPU”, “cuda/cuda-like”, “xpu”. Passing 在  设备 type 使用  GPU index, such as “cuda:0”, 是 not allowed.

mesh_shape (Tuple[int]) –  tuple defining  dimensions 的  multi-dimensional array describing  layout 的 设备.

mesh_dim_names (Tuple[str], optional) –  tuple 的 mesh dimension names 到 assign 到 each dimension 的  multi-dimensional array describing  layout 的 设备. Its length 必须 match  length 的 mesh_shape. Each string 在 mesh_dim_names 必须 是 unique.

backend_override (Dict[int | str, tuple[str, Options] | str | Options], optional) – Overrides 用于 some 或 all 的  ProcessGroups that 将 是 created 用于 each mesh dimension. Each key 可以 是 either  index 的  dimension 或 its name (if mesh_dim_names 是 provided). Each value 可以 是  tuple containing  name 的  backend 和 its options, 或 just one 的 these two components (在 which case  other 将 是 set 到 its default value).

 DeviceMesh object representing  设备 layout.

Check if  default 进程 group 有 是 initialized.

Check if  MPI backend 是 available.

Check if  NCCL backend 是 available.

Check if  Gloo backend 是 available.

Check if  XCCL backend 是 available.

Check whether this 进程 是 launched 使用 torch.分布式.elastic (aka torchelastic).

 existence 的 TORCHELASTIC_RUN_ID environment variable 是 used as  proxy 到 determine whether  current 进程 是 launched 使用 torchelastic. This 是  reasonable proxy since TORCHELASTIC_RUN_ID maps 到  rendezvous id which 是 always  non-null value indicating  job id 用于 peer discovery purposes..

Return  default backend 用于  given 设备.

设备 (Union[str, torch.设备]) –  设备 到 get  default backend 用于.

 default backend 用于  given 设备 as  lower case string.

Currently three 初始化 methods 是 supported:

There 是 two ways 到 initialize using TCP, both requiring  网络 address reachable 从 all 进程 和  desired world_size.  first way requires specifying  address that belongs 到  rank 0 进程. This 初始化 method requires that all 进程 有 manually specified ranks.

Note that multicast address 是 not supported anymore 在  latest 分布式 package. group_name 是 deprecated as well.

Another 初始化 method makes use 的  file system that 是 shared 和 visible 从 all machines 在  group, along 使用  desired world_size.  URL 应该 start 使用 file:// 和 contain  path 到  non-existent file (在  existing directory) 在  shared file system. File-system 初始化 将 automatically create that file if it doesn’t exist, but 将 not delete  file. Therefore, it 是 your responsibility 到 make sure that  file 是 cleaned up before  next init_process_group() call 在  same file path/name.

Note that automatic rank assignment 是 not supported anymore 在  latest 分布式 package 和 group_name 是 deprecated as well.

This method assumes that  file system supports locking using fcntl - most local systems 和 NFS support it.

This method 将 always create  file 和 try its best 到 clean up 和 remove  file at  end 的  program. 在 other words, each 初始化 使用  file init method 将 need  brand new empty file 在 order 用于  初始化 到 succeed. If  same file used 由  previous 初始化 (which happens not 到 get cleaned up) 是 used again, this 是 unexpected behavior 和 可以 often cause deadlocks 和 failures. Therefore, even though this method 将 try its best 到 clean up  file, if  auto-delete happens 到 是 unsuccessful, it 是 your responsibility 到 ensure that  file 是 removed at  end 的  训练 到 prevent  same file 到 是 reused again during  next time. This 是 especially important if you plan 到 call init_process_group() multiple times 在  same file name. 在 other words, if  file 是 not removed/cleaned up 和 you call init_process_group() again 在 that file, failures 是 expected.  rule 的 thumb here 是 that, make sure that  file 是 non-existent 或 empty every time init_process_group() 是 called.

This method 将 read  配置 从 environment variables, allowing one 到 fully customize how  information 是 obtained.  variables 到 是 set 是:

MASTER_PORT - required; 有 到 是  free port 在 machine 使用 rank 0

MASTER_ADDR - required (except 用于 rank 0); address 的 rank 0 node

WORLD_SIZE - required; 可以 是 set either here, 或 在  call 到 init 函数

RANK - required; 可以 是 set either here, 或 在  call 到 init 函数

 machine 使用 rank 0 将 是 used 到 set up all connections.

This 是  default method, meaning that init_method 做 not 有 到 是 specified (或 可以 是 env://).

TORCH_GLOO_LAZY_INIT - establishes connections 在 demand rather than using  full mesh which 可以 greatly improve 初始化 time 用于 non all2all 操作.

Once torch.分布式.init_process_group() 是 run,  following functions 可以 是 used. 到 check whether  进程 group 有 already 是 initialized use torch.分布式.is_initialized().

 enum-like class 用于 backends.

Available backends: GLOO, NCCL, UCC, MPI, XCCL, 和 other registered backends.

 values 的 this class 是 lowercase strings, e.g., "gloo". They 可以 是 accessed as attributes, e.g., Backend.NCCL.

This class 可以 是 directly called 到 parse  string, e.g., Backend(backend_str) 将 check if backend_str 是 valid, 和 return  parsed lowercase string if so. It also accepts uppercase strings, e.g., Backend("GLOO") returns "gloo".

 entry Backend.UNDEFINED 是 present but only used as initial value 的 some fields. Users 应该 neither use it directly nor assume its existence.

Register  new backend 使用  given name 和 instantiating 函数.

This class method 是 used 由 3rd party 进程组 extension 到 register new backends.

name (str) – Backend name 的  进程组 extension. It 应该 match  one 在 init_process_group().

func (函数) – 函数 handler that instantiates  backend.  函数 应该 是 implemented 在  backend extension 和 takes four arguments, including store, rank, world_size, 和 timeout.

extended_api (bool, optional) – Whether  backend supports extended argument structure. Default: False. If set 到 True,  backend 将 get  instance 的 c10d::DistributedBackendOptions, 和  进程 group options object as defined 由  backend 实现.

设备 (str 或 list 的 str, optional) – 设备 type this backend supports, e.g. “CPU”, “cuda”, etc. If None, assuming both “CPU” 和 “cuda”

This support 的 3rd party backend 是 experimental 和 subject 到 change.

Return  backend 的  given 进程 group.

group (进程组, optional) –  进程 group 到 work 在.  default 是  general main 进程 group. If another specific group 是 specified,  calling 进程 必须 是 part 的 group.

 backend 的  given 进程 group as  lower case string.

Return  rank 的  current 进程 在  provided group, default otherwise.

Rank 是  unique identifier assigned 到 each 进程 within  分布式 进程 group. They 是 always consecutive integers ranging 从 0 到 world_size.

group (进程组, optional) –  进程 group 到 work 在. If None,  default 进程 group 将 是 used.

 rank 的  进程 group -1, if not part 的  group

Return  number 的 进程 在  current 进程 group.

group (进程组, optional) –  进程 group 到 work 在. If None,  default 进程 group 将 是 used.

 world size 的  进程 group -1, if not part 的  group

It 是 important 到 clean up resources 在 exit 由 calling destroy_process_group().

 simplest pattern 到 follow 是 到 destroy every 进程 group 和 backend 由 calling destroy_process_group() 使用  default value 的 None 用于  group argument, at  point 在  训练 script where communications 是 no longer needed, usually near  end 的 main().  call 应该 是 made once per trainer-进程, not at  outer 进程-launcher level.

if destroy_process_group() 是 not called 由 all ranks 在  pg within  timeout duration, especially when there 是 multiple 进程-groups 在  application e.g. 用于 N-D parallelism, hangs 在 exit 是 possible. This 是 because  destructor 用于 ProcessGroupNCCL calls ncclCommAbort, which 必须 是 called collectively, but  order 的 calling ProcessGroupNCCL’s destructor if called 由 python’s GC 是 not deterministic. Calling destroy_process_group() helps 由 ensuring ncclCommAbort 是 called 在  consistent order across ranks, 和 avoids calling ncclCommAbort during ProcessGroupNCCL’s destructor.

destroy_process_group 可以 also 是 used 到 destroy individual 进程 groups. One use case 可以 是 fault tolerant 训练, where  进程 group 可能 是 destroyed 和 then  new one initialized during runtime. 在 this case, it’s critical 到 synchronize  trainer 进程 using some means other than torch.分布式 primitives _after_ calling destroy 和 before subsequently initializing. This behavior 是 currently unsupported/untested, due 到  difficulty 的 achieving this 同步, 和 是 considered  known issue. Please file  github issue 或 RFC if this 是  use case that’s blocking you.

由 default collectives operate 在  default group (also called  world) 和 require all 进程 到 enter  分布式 函数 call. However, some workloads 可以 benefit 从 more fine-grained 通信. This 是 where 分布式 groups come into play. new_group() 函数 可以 是 used 到 create new groups, 使用 arbitrary subsets 的 all 进程. It returns  opaque group handle that 可以 是 given as  group argument 到 all collectives (collectives 是 分布式 functions 到 exchange information 在 certain well-known programming patterns).

Create  new 分布式 group.

This 函数 requires that all 进程 在  main group (i.e. all 进程 that 是 part 的  分布式 job) enter this 函数, even if they 是 not going 到 是 members 的  group. Additionally, groups 应该 是 created 在  same order 在 all 进程.

Safe concurrent usage: When using multiple 进程 groups 使用  NCCL backend,  user 必须 ensure  globally consistent execution order 的 collectives across ranks.

If multiple threads within  进程 issue collectives, explicit 同步 是 necessary 到 ensure consistent ordering.

When using async variants 的 torch.分布式 通信 APIs,  work object 是 returned 和  通信 kernel 是 enqueued 在  separate CUDA stream, allowing 重叠 的 通信 和 计算. Once one 或 more async ops 有 是 issued 在 one 进程 group, they 必须 是 synchronized 使用 other cuda streams 由 calling work.wait() before using another 进程 group.

See Using multiple NCCL communicators concurrently <https://docs.nvidia.com/deeplearning/nccl/user-guide/docs/usage/communicators.html#using-multiple-nccl-communicators-concurrently> 用于 more details.

ranks (list[int]) – List 的 ranks 的 group members. If None, 将 是 set 到 all ranks. Default 是 None.

timeout (timedelta, optional) – see init_process_group 用于 details 和 default value.

backend (str 或 Backend, optional) –  backend 到 use. Depending 在 build-time configurations, valid values 是 gloo 和 nccl. 由 default uses  same backend as  global group. This field 应该 是 given as  lowercase string (e.g., "gloo"), which 可以 also 是 accessed via Backend attributes (e.g., Backend.GLOO). If None 是 passed 在,  backend corresponding 到  default 进程 group 将 是 used. Default 是 None.

pg_options (ProcessGroupOptions, optional) – 进程 group options specifying what additional options need 到 是 passed 在 during  构造 的 specific 进程 groups. i.e. 用于  nccl backend, is_high_priority_stream 可以 是 specified so that 进程 group 可以 pick up high priority cuda streams. 用于 other available options 到 config nccl, See https://docs.nvidia.com/deeplearning/nccl/user-guide/docs/API/types.html#ncclconfig-tuse_local_synchronization (bool, optional): perform  group-local barrier at  end 的  进程 group creation. This 是 different 在 that non-member ranks don’t need 到 call into API 和 don’t join  barrier.

group_desc (str, optional) –  string 到 describe  进程 group.

device_id (torch.设备, optional) –  single, specific 设备 到 “bind” this 进程 到,  new_group call 将 try 到 initialize  通信 backend immediately 用于  设备 if this field 是 given.

 handle 的 分布式 group that 可以 是 given 到 collective calls 或 GroupMember.NON_GROUP_MEMBER if  rank 是 not part 的 ranks.

N.B. use_local_synchronization doesn’t work 使用 MPI.

N.B. While use_local_synchronization=True 可以 是 significantly faster 使用 larger clusters 和 small 进程 groups, care 必须 是 taken since it changes cluster behavior as non-member ranks don’t join  group barrier().

N.B. use_local_synchronization=True 可以 lead 到 deadlocks when each rank creates multiple overlapping 进程 groups. 到 avoid that, make sure all ranks follow  same global creation order.

Translate  global rank into  group rank.

global_rank 必须 是 part 的 group otherwise this raises RuntimeError.

group (进程组) – 进程组 到 find  relative rank.

global_rank (int) – Global rank 到 query.

Group rank 的 global_rank relative 到 group

N.B. calling this 函数 在  default 进程 group returns identity

Translate  group rank into  global rank.

group_rank 必须 是 part 的 group otherwise this raises RuntimeError.

group (进程组) – 进程组 到 find  global rank 从.

group_rank (int) – Group rank 到 query.

Global rank 的 group_rank relative 到 group

N.B. calling this 函数 在  default 进程 group returns identity

Get all ranks associated 使用 group.

group (Optional[进程组]) – 进程组 到 get all ranks 从. If None,  default 进程 group 将 是 used.

List 的 global ranks ordered 由 group rank.

DeviceMesh 是  higher level abstraction that manages 进程 groups (或 NCCL communicators). It allows user 到 easily create inter node 和 intra node 进程 groups without worrying about how 到 set up  ranks correctly 用于 different sub 进程 groups, 和 it helps manage those 分布式 进程 group easily. init_device_mesh() 函数 可以 是 used 到 create new DeviceMesh, 使用  mesh shape describing  设备 topology.

DeviceMesh represents  mesh 的 设备, where layout 的 设备 可以 是 represented as  n-d dimension array, 和 each value 的  n-d dimensional array 是  global id 的  default 进程 group ranks.

DeviceMesh 可以 是 used 到 setup  N dimensional 设备 connections across  cluster, 和 manage  ProcessGroups 用于 N dimensional parallelisms. Communications 可以 happen 在 each dimension 的  DeviceMesh separately. DeviceMesh respects  设备 that user selects already (i.e. if user call torch.cuda.set_device before  DeviceMesh 初始化), 和 将 select/set  设备 用于  current 进程 if user 做 not set  设备 beforehand. Note that manual 设备 selection 应该 happen BEFORE  DeviceMesh 初始化.

DeviceMesh 可以 also 是 used as  context manager when using together 使用 DTensor APIs.

DeviceMesh follows SPMD programming 模型, which means  same PyTorch Python program 是 running 在 all 进程/ranks 在  cluster. Therefore, users need 到 make sure  mesh array (which describes  layout 的 设备) 应该 是 identical across all ranks. Inconsistent mesh 将 lead 到 silent hang.

device_type (str) –  设备 type 的  mesh. Currently supports: “CPU”, “cuda/cuda-like”.

mesh (ndarray) –  multi-dimensional array 或  integer 张量 describing  layout 的 设备, where  IDs 是 global IDs 的  default 进程 group.

 DeviceMesh object representing  设备 layout.

 following program runs 在 each 进程/rank 在  SPMD manner. 在 this 示例, we 有 2 hosts 使用 4 GPUs each.  reduction over  first dimension 的 mesh 将 归约 across columns (0, 4), .. 和 (3, 7),  reduction over  second dimension 的 mesh reduces across rows (0, 1, 2, 3) 和 (4, 5, 6, 7).

Constructs  DeviceMesh 使用 device_type 从  existing 进程组 或  list 的 existing 进程组.

 constructed 设备 mesh 有 number 的 dimensions equal 到  number 的 groups passed. 用于 示例, if  single 进程 group 是 passed 在,  resulted DeviceMesh 是  1D mesh. If  list 的 2 进程 groups 是 passed 在,  resulted DeviceMesh 是  2D mesh.

If more than one group 是 passed, then  mesh 和 mesh_dim_names arguments 是 required.  order 的  进程 groups passed 在 determines  topology 的  mesh. 用于 示例,  first 进程 group 将 是  0th dimension 的  DeviceMesh.  mesh 张量 passed 在 必须 有  same number 的 dimensions as  number 的 进程 groups passed 在, 和  order 的  dimensions 在  mesh 张量 必须 match  order 在  进程 groups passed 在.

group (进程组 或 list[进程组]) –  existing 进程组 或  list 的 existing ProcessGroups.

device_type (str) –  设备 type 的  mesh. Currently supports: “CPU”, “cuda/cuda-like”. Passing 在  设备 type 使用  GPU index, such as “cuda:0”, 是 not allowed.

mesh (torch.张量 或 ArrayLike, optional) –  multi-dimensional array 或  integer 张量 describing  layout 的 设备, where  IDs 是 global IDs 的  default 进程 group. Default 是 None.

mesh_dim_names (tuple[str], optional) –  tuple 的 mesh dimension names 到 assign 到 each dimension 的  multi-dimensional array describing  layout 的 设备. Its length 必须 match  length 的 mesh_shape. Each string 在 mesh_dim_names 必须 是 unique. Default 是 None.

 DeviceMesh object representing  设备 layout.

Returns  list 的 ProcessGroups 用于 all mesh dimensions.

 list 的 进程组 object.

list[torch.分布式.distributed_c10d.进程组]

Return  relative indices 的 this rank relative 到 all dimensions 的  mesh. If this rank 是 not part 的  mesh, return None.

Returns  single 进程组 specified 由 mesh_dim, 或, if mesh_dim 是 not specified 和  DeviceMesh 是 1-dimensional, returns  only 进程组 在  mesh.

mesh_dim (str/python:int, optional) – it 可以 是  name 的  mesh dimension 或  index

None. (的  mesh dimension. Default 是) –

 进程组 object.

Returns  local rank 的  given mesh_dim 的  DeviceMesh.

mesh_dim (str/python:int, optional) – it 可以 是  name 的  mesh dimension 或  index

None. (的  mesh dimension. Default 是) –

 integer denotes  local rank.

 following program runs 在 each 进程/rank 在  SPMD manner. 在 this 示例, we 有 2 hosts 使用 4 GPUs each. Calling mesh_2d.get_local_rank(mesh_dim=0) 在 rank 0, 1, 2, 3 将 return 0. Calling mesh_2d.get_local_rank(mesh_dim=0) 在 rank 4, 5, 6, 7 将 return 1. Calling mesh_2d.get_local_rank(mesh_dim=1) 在 rank 0, 4 将 return 0. Calling mesh_2d.get_local_rank(mesh_dim=1) 在 rank 1, 5 将 return 1. Calling mesh_2d.get_local_rank(mesh_dim=1) 在 rank 2, 6 将 return 2. Calling mesh_2d.get_local_rank(mesh_dim=1) 在 rank 3, 7 将 return 3.

Returns  current global rank.

Send  张量 synchronously.

tag 是 not supported 使用  NCCL backend.

张量 (张量) – 张量 到 send.

dst (int) – Destination rank 在 global 进程 group (regardless 的 group argument). Destination rank 应该 not 是  same as  rank 的  current 进程.

group (进程组, optional) –  进程 group 到 work 在. If None,  default 进程 group 将 是 used.

tag (int, optional) – Tag 到 match send 使用 remote recv

group_dst (int, optional) – Destination rank 在 group. Invalid 到 specify both dst 和 group_dst.

Receives  张量 synchronously.

tag 是 not supported 使用  NCCL backend.

张量 (张量) – 张量 到 fill 使用 received 数据.

src (int, optional) – Source rank 在 global 进程 group (regardless 的 group argument). 将 receive 从 any 进程 if unspecified.

group (进程组, optional) –  进程 group 到 work 在. If None,  default 进程 group 将 是 used.

tag (int, optional) – Tag 到 match recv 使用 remote send

group_src (int, optional) – Destination rank 在 group. Invalid 到 specify both src 和 group_src.

Sender rank -1, if not part 的  group

isend() 和 irecv() return 分布式 request objects when used. 在 general,  type 的 this object 是 unspecified as they 应该 never 是 created manually, but they 是 guaranteed 到 support two methods:

is_completed() - returns True if  操作 有 finished

wait() - 将 block  进程 until  操作 是 finished. is_completed() 是 guaranteed 到 return True once it returns.

Send  张量 asynchronously.

Modifying 张量 before  request completes causes undefined behavior.

tag 是 not supported 使用  NCCL backend.

Unlike send, which 是 blocking, isend allows src == dst rank, i.e. send 到 self.

张量 (张量) – 张量 到 send.

dst (int) – Destination rank 在 global 进程 group (regardless 的 group argument)

group (进程组, optional) –  进程 group 到 work 在. If None,  default 进程 group 将 是 used.

tag (int, optional) – Tag 到 match send 使用 remote recv

group_dst (int, optional) – Destination rank 在 group. Invalid 到 specify both dst 和 group_dst

 分布式 request object. None, if not part 的  group

Receives  张量 asynchronously.

tag 是 not supported 使用  NCCL backend.

Unlike recv, which 是 blocking, irecv allows src == dst rank, i.e. recv 从 self.

张量 (张量) – 张量 到 fill 使用 received 数据.

src (int, optional) – Source rank 在 global 进程 group (regardless 的 group argument). 将 receive 从 any 进程 if unspecified.

group (进程组, optional) –  进程 group 到 work 在. If None,  default 进程 group 将 是 used.

tag (int, optional) – Tag 到 match recv 使用 remote send

group_src (int, optional) – Destination rank 在 group. Invalid 到 specify both src 和 group_src.

 分布式 request object. None, if not part 的  group

Sends picklable objects 在 object_list synchronously.

Similar 到 send(), but Python objects 可以 是 passed 在. Note that all objects 在 object_list 必须 是 picklable 在 order 到 是 sent.

object_list (List[Any]) – List 的 输入 objects 到 sent. Each object 必须 是 picklable. Receiver 必须 provide lists 的 equal sizes.

dst (int) – Destination rank 到 send object_list 到. Destination rank 是 based 在 global 进程 group (regardless 的 group argument)

group (Optional[进程组]) – (进程组, optional):  进程 group 到 work 在. If None,  default 进程 group 将 是 used. Default 是 None.

设备 (torch.设备, optional) – If not None,  objects 是 serialized 和 converted 到 tensors which 是 moved 到  设备 before sending. Default 是 None.

group_dst (int, optional) – Destination rank 在 group. 必须 specify one 的 dst 和 group_dst but not both

use_batch (bool, optional) – If True, use 批次 p2p 操作 instead 的 regular send 操作. This avoids initializing 2-rank communicators 和 uses existing entire group communicators. See batch_isend_irecv 用于 usage 和 assumptions. Default 是 False.

用于 NCCL-based 进程 groups, internal 张量 representations 的 objects 必须 是 moved 到  GPU 设备 before 通信 takes place. 在 this case,  设备 used 是 given 由 torch.cuda.current_device() 和 it 是  user’s responsibility 到 ensure that this 是 set so that each rank 有  individual GPU, via torch.cuda.set_device().

Object collectives 有  number 的 serious 性能 和 scalability limitations. See Object collectives 用于 details.

send_object_list() uses pickle 模块 implicitly, which 是 known 到 是 insecure. It 是 possible 到 construct malicious pickle 数据 which 将 execute arbitrary code during unpickling. Only call this 函数 使用 数据 you trust.

Calling send_object_list() 使用 GPU tensors 是 not well supported 和 inefficient as it incurs GPU -> CPU transfer since tensors 将 是 pickled. Please consider using send() instead.

Receives picklable objects 在 object_list synchronously.

Similar 到 recv(), but 可以 receive Python objects.

object_list (List[Any]) – List 的 objects 到 receive into. 必须 provide  list 的 sizes equal 到  size 的  list 正在 sent.

src (int, optional) – Source rank 从 which 到 recv object_list. Source rank 是 based 在 global 进程 group (regardless 的 group argument) 将 receive 从 any rank if set 到 None. Default 是 None.

group (Optional[进程组]) – (进程组, optional):  进程 group 到 work 在. If None,  default 进程 group 将 是 used. Default 是 None.

设备 (torch.设备, optional) – If not None, receives 在 this 设备. Default 是 None.

group_src (int, optional) – Destination rank 在 group. Invalid 到 specify both src 和 group_src.

use_batch (bool, optional) – If True, use 批次 p2p 操作 instead 的 regular send 操作. This avoids initializing 2-rank communicators 和 uses existing entire group communicators. See batch_isend_irecv 用于 usage 和 assumptions. Default 是 False.

Sender rank. -1 if rank 是 not part 的  group. If rank 是 part 的  group, object_list 将 contain  sent objects 从 src rank.

用于 NCCL-based 进程 groups, internal 张量 representations 的 objects 必须 是 moved 到  GPU 设备 before 通信 takes place. 在 this case,  设备 used 是 given 由 torch.cuda.current_device() 和 it 是  user’s responsibility 到 ensure that this 是 set so that each rank 有  individual GPU, via torch.cuda.set_device().

Object collectives 有  number 的 serious 性能 和 scalability limitations. See Object collectives 用于 details.

recv_object_list() uses pickle 模块 implicitly, which 是 known 到 是 insecure. It 是 possible 到 construct malicious pickle 数据 which 将 execute arbitrary code during unpickling. Only call this 函数 使用 数据 you trust.

Calling recv_object_list() 使用 GPU tensors 是 not well supported 和 inefficient as it incurs GPU -> CPU transfer since tensors 将 是 pickled. Please consider using recv() instead.

Send 或 Receive  批次 的 tensors asynchronously 和 return  list 的 requests.

进程 each 的  操作 在 p2p_op_list 和 return  corresponding requests. NCCL, Gloo, 和 UCC backend 是 currently supported.

p2p_op_list (list[torch.分布式.distributed_c10d.P2POp]) –  list 的 point-到-point 操作(type 的 each operator 是 torch.分布式.P2POp).  order 的  isend/irecv 在  list matters 和 it needs 到 match 使用 corresponding isend/irecv 在  remote end.

 list 的 分布式 request objects returned 由 calling  corresponding op 在  op_list.

list[torch.分布式.distributed_c10d.Work]

Note that when this API 是 used 使用  NCCL PG backend, users 必须 set  current GPU 设备 使用 torch.cuda.set_device, otherwise it 将 lead 到 unexpected hang issues.

在 addition, if this API 是  first collective call 在  group passed 到 dist.P2POp, all ranks 的  group 必须 participate 在 this API call; otherwise,  behavior 是 undefined. If this API call 是 not  first collective call 在  group, batched P2P 操作 involving only  subset 的 ranks 的  group 是 allowed.

 class 到 build point-到-point 操作 用于 batch_isend_irecv.

This class builds  type 的 P2P 操作, 通信 buffer, peer rank, 进程 Group, 和 tag. Instances 的 this class 将 是 passed 到 batch_isend_irecv 用于 point-到-point communications.

op (Callable) –  函数 到 send 数据 到 或 receive 数据 从  peer 进程.  type 的 op 是 either torch.分布式.isend 或 torch.分布式.irecv.

张量 (张量) – 张量 到 send 或 receive.

peer (int, optional) – Destination 或 source rank.

group (进程组, optional) –  进程 group 到 work 在. If None,  default 进程 group 将 是 used.

tag (int, optional) – Tag 到 match send 使用 recv.

group_peer (int, optional) – Destination 或 source rank.

Every collective 操作 函数 supports  following two kinds 的 操作, depending 在  setting 的  async_op flag passed into  collective:

Synchronous 操作 -  default mode, when async_op 是 set 到 False. When  函数 returns, it 是 guaranteed that  collective 操作 是 performed. 在  case 的 CUDA 操作, it 是 not guaranteed that  CUDA 操作 是 completed, since CUDA 操作 是 asynchronous. 用于 CPU collectives, any further 函数 calls utilizing  输出 的  collective call 将 behave as expected. 用于 CUDA collectives, 函数 calls utilizing  输出 在  same CUDA stream 将 behave as expected. Users 必须 take care 的 同步 under  scenario 的 running under different streams. 用于 details 在 CUDA semantics such as stream 同步, see CUDA Semantics. See  below script 到 see examples 的 differences 在 these semantics 用于 CPU 和 CUDA 操作.

Asynchronous 操作 - when async_op 是 set 到 True.  collective 操作 函数 returns  分布式 request object. 在 general, you don’t need 到 create it manually 和 it 是 guaranteed 到 support two methods:

is_completed() - 在  case 的 CPU collectives, returns True if completed. 在  case 的 CUDA 操作, returns True if  操作 有 是 successfully enqueued onto  CUDA stream 和  输出 可以 是 utilized 在  default stream without further 同步.

wait() - 在  case 的 CPU collectives, 将 block  进程 until  操作 是 completed. 在  case 的 CUDA collectives, 将 block  currently active CUDA stream until  操作 是 completed (but 将 not block  CPU).

get_future() - returns torch._C.Future object. Supported 用于 NCCL, also supported 用于 most 操作 在 GLOO 和 MPI, except 用于 peer 到 peer 操作. Note: as we continue adopting Futures 和 merging APIs, get_future() call 可能 become redundant.

 following code 可以 serve as  reference regarding semantics 用于 CUDA 操作 when using 分布式 collectives. It shows  explicit need 到 synchronize when using collective outputs 在 different CUDA streams:

Broadcasts  张量 到  whole group.

张量 必须 有  same number 的 elements 在 all 进程 participating 在  collective.

张量 (张量) – 数据 到 是 sent if src 是  rank 的 current 进程, 和 张量 到 是 used 到 save received 数据 otherwise.

src (int) – Source rank 在 global 进程 group (regardless 的 group argument).

group (进程组, optional) –  进程 group 到 work 在. If None,  default 进程 group 将 是 used.

async_op (bool, optional) – Whether this op 应该 是  async op

group_src (int) – Source rank 在 group. 必须 specify one 的 group_src 和 src but not both.

Async work handle, if async_op 是 set 到 True. None, if not async_op 或 if not part 的  group

Broadcasts picklable objects 在 object_list 到  whole group.

Similar 到 广播(), but Python objects 可以 是 passed 在. Note that all objects 在 object_list 必须 是 picklable 在 order 到 是 broadcasted.

object_list (List[Any]) – List 的 输入 objects 到 广播. Each object 必须 是 picklable. Only objects 在  src rank 将 是 广播, but each rank 必须 provide lists 的 equal sizes.

src (int) – Source rank 从 which 到 广播 object_list. Source rank 是 based 在 global 进程 group (regardless 的 group argument)

group (Optional[进程组]) – (进程组, optional):  进程 group 到 work 在. If None,  default 进程 group 将 是 used. Default 是 None.

设备 (torch.设备, optional) – If not None,  objects 是 serialized 和 converted 到 tensors which 是 moved 到  设备 before broadcasting. Default 是 None.

group_src (int) – Source rank 在 group. 必须 not specify one 的 group_src 和 src but not both.

None. If rank 是 part 的  group, object_list 将 contain  broadcasted objects 从 src rank.

用于 NCCL-based 进程 groups, internal 张量 representations 的 objects 必须 是 moved 到  GPU 设备 before 通信 takes place. 在 this case,  设备 used 是 given 由 torch.cuda.current_device() 和 it 是  user’s responsibility 到 ensure that this 是 set so that each rank 有  individual GPU, via torch.cuda.set_device().

Note that this API differs slightly 从  广播() collective since it 做 not provide  async_op handle 和 thus 将 是  blocking call.

Object collectives 有  number 的 serious 性能 和 scalability limitations. See Object collectives 用于 details.

broadcast_object_list() uses pickle 模块 implicitly, which 是 known 到 是 insecure. It 是 possible 到 construct malicious pickle 数据 which 将 execute arbitrary code during unpickling. Only call this 函数 使用 数据 you trust.

Calling broadcast_object_list() 使用 GPU tensors 是 not well supported 和 inefficient as it incurs GPU -> CPU transfer since tensors 将 是 pickled. Please consider using 广播() instead.

Reduces  张量 数据 across all machines 在  way that all get  final result.

After  call 张量 是 going 到 是 bitwise identical 在 all 进程.

Complex tensors 是 supported.

张量 (张量) – 输入 和 输出 的  collective.  函数 operates 在-place.

op (optional) – One 的  values 从 torch.分布式.ReduceOp enum. Specifies  操作 used 用于 element-wise reductions.

group (进程组, optional) –  进程 group 到 work 在. If None,  default 进程 group 将 是 used.

async_op (bool, optional) – Whether this op 应该 是  async op

Async work handle, if async_op 是 set 到 True. None, if not async_op 或 if not part 的  group

Reduces  张量 数据 across all machines.

Only  进程 使用 rank dst 是 going 到 receive  final result.

张量 (张量) – 输入 和 输出 的  collective.  函数 operates 在-place.

dst (int) – Destination rank 在 global 进程 group (regardless 的 group argument)

op (optional) – One 的  values 从 torch.分布式.ReduceOp enum. Specifies  操作 used 用于 element-wise reductions.

group (进程组, optional) –  进程 group 到 work 在. If None,  default 进程 group 将 是 used.

async_op (bool, optional) – Whether this op 应该 是  async op

group_dst (int) – Destination rank 在 group. 必须 specify one 的 group_dst 和 dst but not both.

Async work handle, if async_op 是 set 到 True. None, if not async_op 或 if not part 的  group

Gathers tensors 从  whole group 在  list.

Complex 和 uneven sized tensors 是 supported.

tensor_list (list[张量]) – 输出 list. It 应该 contain correctly-sized tensors 到 是 used 用于 输出 的  collective. Uneven sized tensors 是 supported.

张量 (张量) – 张量 到 是 广播 从 current 进程.

group (进程组, optional) –  进程 group 到 work 在. If None,  default 进程 group 将 是 used.

async_op (bool, optional) – Whether this op 应该 是  async op

Async work handle, if async_op 是 set 到 True. None, if not async_op 或 if not part 的  group

Gather tensors 从 all ranks 和 put them 在  single 输出 张量.

This 函数 requires all tensors 到 是  same size 在 each 进程.

output_tensor (张量) – 输出 张量 到 accommodate 张量 elements 从 all ranks. It 必须 是 correctly sized 到 有 one 的  following forms: (i)  concatenation 的 all  输入 tensors along  primary dimension; 用于 definition 的 “concatenation”, see torch.cat(); (ii)  stack 的 all  输入 tensors along  primary dimension; 用于 definition 的 “stack”, see torch.stack(). Examples below 可能 better explain  supported 输出 forms.

input_tensor (张量) – 张量 到 是 gathered 从 current rank. Different 从  all_gather API,  输入 tensors 在 this API 必须 有  same size across all ranks.

group (进程组, optional) –  进程 group 到 work 在. If None,  default 进程 group 将 是 used.

async_op (bool, optional) – Whether this op 应该 是  async op

Async work handle, if async_op 是 set 到 True. None, if not async_op 或 if not part 的  group

Gathers picklable objects 从  whole group into  list.

Similar 到 all_gather(), but Python objects 可以 是 passed 在. Note that  object 必须 是 picklable 在 order 到 是 gathered.

object_list (list[Any]) – 输出 list. It 应该 是 correctly sized as  size 的  group 用于 this collective 和 将 contain  输出.

obj (Any) – Pickable Python object 到 是 广播 从 current 进程.

group (进程组, optional) –  进程 group 到 work 在. If None,  default 进程 group 将 是 used. Default 是 None.

None. If  calling rank 是 part 的 this group,  输出 的  collective 将 是 populated into  输入 object_list. If  calling rank 是 not part 的  group,  passed 在 object_list 将 是 unmodified.

Note that this API differs slightly 从  all_gather() collective since it 做 not provide  async_op handle 和 thus 将 是  blocking call.

用于 NCCL-based processed groups, internal 张量 representations 的 objects 必须 是 moved 到  GPU 设备 before 通信 takes place. 在 this case,  设备 used 是 given 由 torch.cuda.current_device() 和 it 是  user’s responsibility 到 ensure that this 是 set so that each rank 有  individual GPU, via torch.cuda.set_device().

Object collectives 有  number 的 serious 性能 和 scalability limitations. See Object collectives 用于 details.

all_gather_object() uses pickle 模块 implicitly, which 是 known 到 是 insecure. It 是 possible 到 construct malicious pickle 数据 which 将 execute arbitrary code during unpickling. Only call this 函数 使用 数据 you trust.

Calling all_gather_object() 使用 GPU tensors 是 not well supported 和 inefficient as it incurs GPU -> CPU transfer since tensors 将 是 pickled. Please consider using all_gather() instead.

Gathers  list 的 tensors 在  single 进程.

This 函数 requires all tensors 到 是  same size 在 each 进程.

张量 (张量) – 输入 张量.

gather_list (list[张量], optional) – List 的 appropriately, same-sized tensors 到 use 用于 gathered 数据 (default 是 None, 必须 是 specified 在  destination rank)

dst (int, optional) – Destination rank 在 global 进程 group (regardless 的 group argument). (If both dst 和 group_dst 是 None, default 是 global rank 0)

group (进程组, optional) –  进程 group 到 work 在. If None,  default 进程 group 将 是 used.

async_op (bool, optional) – Whether this op 应该 是  async op

group_dst (int, optional) – Destination rank 在 group. Invalid 到 specify both dst 和 group_dst

Async work handle, if async_op 是 set 到 True. None, if not async_op 或 if not part 的  group

Note that all Tensors 在 gather_list 必须 有  same size.

Gathers picklable objects 从  whole group 在  single 进程.

Similar 到 gather(), but Python objects 可以 是 passed 在. Note that  object 必须 是 picklable 在 order 到 是 gathered.

obj (Any) – 输入 object. 必须 是 picklable.

object_gather_list (list[Any]) – 输出 list. 在  dst rank, it 应该 是 correctly sized as  size 的  group 用于 this collective 和 将 contain  输出. 必须 是 None 在 non-dst ranks. (default 是 None)

dst (int, optional) – Destination rank 在 global 进程 group (regardless 的 group argument). (If both dst 和 group_dst 是 None, default 是 global rank 0)

group (Optional[进程组]) – (进程组, optional):  进程 group 到 work 在. If None,  default 进程 group 将 是 used. Default 是 None.

group_dst (int, optional) – Destination rank 在 group. Invalid 到 specify both dst 和 group_dst

None. 在  dst rank, object_gather_list 将 contain  输出 的  collective.

Note that this API differs slightly 从  gather collective since it 做 not provide  async_op handle 和 thus 将 是  blocking call.

用于 NCCL-based processed groups, internal 张量 representations 的 objects 必须 是 moved 到  GPU 设备 before 通信 takes place. 在 this case,  设备 used 是 given 由 torch.cuda.current_device() 和 it 是  user’s responsibility 到 ensure that this 是 set so that each rank 有  individual GPU, via torch.cuda.set_device().

Object collectives 有  number 的 serious 性能 和 scalability limitations. See Object collectives 用于 details.

gather_object() uses pickle 模块 implicitly, which 是 known 到 是 insecure. It 是 possible 到 construct malicious pickle 数据 which 将 execute arbitrary code during unpickling. Only call this 函数 使用 数据 you trust.

Calling gather_object() 使用 GPU tensors 是 not well supported 和 inefficient as it incurs GPU -> CPU transfer since tensors 将 是 pickled. Please consider using gather() instead.

Scatters  list 的 tensors 到 all 进程 在  group.

Each 进程 将 receive exactly one 张量 和 store its 数据 在  张量 argument.

Complex tensors 是 supported.

张量 (张量) – 输出 张量.

scatter_list (list[张量]) – List 的 tensors 到 scatter (default 是 None, 必须 是 specified 在  source rank)

src (int) – Source rank 在 global 进程 group (regardless 的 group argument). (If both src 和 group_src 是 None, default 是 global rank 0)

group (进程组, optional) –  进程 group 到 work 在. If None,  default 进程 group 将 是 used.

async_op (bool, optional) – Whether this op 应该 是  async op

group_src (int, optional) – Source rank 在 group. Invalid 到 specify both src 和 group_src

Async work handle, if async_op 是 set 到 True. None, if not async_op 或 if not part 的  group

Note that all Tensors 在 scatter_list 必须 有  same size.

Scatters picklable objects 在 scatter_object_input_list 到  whole group.

Similar 到 scatter(), but Python objects 可以 是 passed 在. 在 each rank,  scattered object 将 是 stored as  first element 的 scatter_object_output_list. Note that all objects 在 scatter_object_input_list 必须 是 picklable 在 order 到 是 scattered.

scatter_object_output_list (List[Any]) – Non-empty list whose first element 将 store  object scattered 到 this rank.

scatter_object_input_list (List[Any], optional) – List 的 输入 objects 到 scatter. Each object 必须 是 picklable. Only objects 在  src rank 将 是 scattered, 和  argument 可以 是 None 用于 non-src ranks.

src (int) – Source rank 从 which 到 scatter scatter_object_input_list. Source rank 是 based 在 global 进程 group (regardless 的 group argument). (If both src 和 group_src 是 None, default 是 global rank 0)

group (Optional[进程组]) – (进程组, optional):  进程 group 到 work 在. If None,  default 进程 group 将 是 used. Default 是 None.

group_src (int, optional) – Source rank 在 group. Invalid 到 specify both src 和 group_src

None. If rank 是 part 的  group, scatter_object_output_list 将 有 its first element set 到  scattered object 用于 this rank.

Note that this API differs slightly 从  scatter collective since it 做 not provide  async_op handle 和 thus 将 是  blocking call.

Object collectives 有  number 的 serious 性能 和 scalability limitations. See Object collectives 用于 details.

scatter_object_list() uses pickle 模块 implicitly, which 是 known 到 是 insecure. It 是 possible 到 construct malicious pickle 数据 which 将 execute arbitrary code during unpickling. Only call this 函数 使用 数据 you trust.

Calling scatter_object_list() 使用 GPU tensors 是 not well supported 和 inefficient as it incurs GPU -> CPU transfer since tensors 将 是 pickled. Please consider using scatter() instead.

Reduces, then scatters  list 的 tensors 到 all 进程 在  group.

输出 (张量) – 输出 张量.

input_list (list[张量]) – List 的 tensors 到 归约 和 scatter.

op (optional) – One 的  values 从 torch.分布式.ReduceOp enum. Specifies  操作 used 用于 element-wise reductions.

group (进程组, optional) –  进程 group 到 work 在. If None,  default 进程 group 将 是 used.

async_op (bool, optional) – Whether this op 应该 是  async op.

Async work handle, if async_op 是 set 到 True. None, if not async_op 或 if not part 的  group.

Reduces, then scatters  张量 到 all ranks 在  group.

输出 (张量) – 输出 张量. It 应该 有  same size across all ranks.

输入 (张量) – 输入 张量 到 是 reduced 和 scattered. Its size 应该 是 输出 张量 size times  world size.  输入 张量 可以 有 one 的  following shapes: (i)  concatenation 的  输出 tensors along  primary dimension, 或 (ii)  stack 的  输出 tensors along  primary dimension. 用于 definition 的 “concatenation”, see torch.cat(). 用于 definition 的 “stack”, see torch.stack().

group (进程组, optional) –  进程 group 到 work 在. If None,  default 进程 group 将 是 used.

async_op (bool, optional) – Whether this op 应该 是  async op.

Async work handle, if async_op 是 set 到 True. None, if not async_op 或 if not part 的  group.

Split 输入 张量 和 then scatter  split list 到 all 进程 在  group.

Later  received tensors 是 concatenated 从 all  进程 在  group 和 returned as  single 输出 张量.

Complex tensors 是 supported.

输出 (张量) – Gathered concatenated 输出 张量.

输入 (张量) – 输入 张量 到 scatter.

output_split_sizes – (list[Int], optional): 输出 split sizes 用于 dim 0 if specified None 或 empty, dim 0 的 输出 张量 必须 divide equally 由 world_size.

input_split_sizes – (list[Int], optional): 输入 split sizes 用于 dim 0 if specified None 或 empty, dim 0 的 输入 张量 必须 divide equally 由 world_size.

group (进程组, optional) –  进程 group 到 work 在. If None,  default 进程 group 将 是 used.

async_op (bool, optional) – Whether this op 应该 是  async op.

Async work handle, if async_op 是 set 到 True. None, if not async_op 或 if not part 的  group.

all_to_all_single 是 experimental 和 subject 到 change.

Scatters list 的 输入 tensors 到 all 进程 在  group 和 return gathered list 的 tensors 在 输出 list.

Complex tensors 是 supported.

output_tensor_list (list[张量]) – List 的 tensors 到 是 gathered one per rank.

input_tensor_list (list[张量]) – List 的 tensors 到 scatter one per rank.

group (进程组, optional) –  进程 group 到 work 在. If None,  default 进程 group 将 是 used.

async_op (bool, optional) – Whether this op 应该 是  async op.

Async work handle, if async_op 是 set 到 True. None, if not async_op 或 if not part 的  group.

all_to_all 是 experimental 和 subject 到 change.

Synchronize all 进程.

This collective blocks 进程 until  whole group enters this 函数, if async_op 是 False, 或 if async work handle 是 called 在 wait().

group (进程组, optional) –  进程 group 到 work 在. If None,  default 进程 group 将 是 used.

async_op (bool, optional) – Whether this op 应该 是  async op

device_ids ([int], optional) – List 的 设备/GPU ids. Only one id 是 expected.

Async work handle, if async_op 是 set 到 True. None, if not async_op 或 if not part 的  group

ProcessGroupNCCL now blocks  CPU thread till  completion 的  barrier collective.

ProcessGroupNCCL implements barrier as  all_reduce 的  1-element 张量.  设备 必须 是 chosen 用于 allocating this 张量.  设备 choice 是 made 由 checking 在 this order (1)  first 设备 passed 到 device_ids arg 的 barrier if not None, (2)  设备 passed 到 init_process_group if not None, (3)  设备 that 是 first used 使用 this 进程 group, if another collective 使用 张量 inputs 有 是 performed, (4)  设备 index indicated 由  global rank mod local 设备 count.

Synchronize 进程 similar 到 torch.分布式.barrier, but consider  configurable timeout.

It 是 able 到 report ranks that 做 not 传播 this barrier within  provided timeout. Specifically, 用于 non-zero ranks, 将 block until  send/recv 是 processed 从 rank 0. Rank 0 将 block until all send /recv 从 other ranks 是 processed, 和 将 report failures 用于 ranks that failed 到 respond 在 time. Note that if one rank 做 not reach  monitored_barrier (用于 示例 due 到  hang), all other ranks 将 fail 在 monitored_barrier.

This collective 将 block all 进程/ranks 在  group, until  whole group exits  函数 successfully, making it useful 用于 debugging 和 synchronizing. However, it 可以 有  性能 impact 和 应该 only 是 used 用于 debugging 或 scenarios that require full 同步 points 在  host-side. 用于 debugging purposes, this barrier 可以 是 inserted before  application’s collective calls 到 check if any ranks 是 desynchronized.

Note that this collective 是 only supported 使用  GLOO backend.

group (进程组, optional) –  进程 group 到 work 在. If None,  default 进程 group 将 是 used.

timeout (datetime.timedelta, optional) – Timeout 用于 monitored_barrier. If None,  default 进程 group timeout 将 是 used.

wait_all_ranks (bool, optional) – Whether 到 collect all failed ranks 或 not. 由 default, this 是 False 和 monitored_barrier 在 rank 0 将 throw 在  first failed rank it encounters 在 order 到 fail fast. 由 setting wait_all_ranks=True monitored_barrier 将 collect all failed ranks 和 throw  error containing information about all failed ranks.

 Work object represents  handle 到  pending asynchronous 操作 在 PyTorch’s 分布式 package. It 是 returned 由 non-blocking collective 操作, such as dist.all_reduce(张量, async_op=True).

Blocks  currently active GPU stream 在  操作 到 complete. 用于 GPU based collectives this 是 equivalent 到 synchronize. 用于 CPU initiated collectives such as 使用 Gloo this 将 block  CUDA stream until  操作 是 complete.

This returns immediately 在 all cases.

到 check whether  操作 是 successful you 应该 check  Work object result asynchronously.

 torch.futures.Future object which 是 associated 使用  completion 的  Work. As  示例,  future object 可以 是 retrieved 由 fut = process_group.全归约(tensors).get_future().

Below 是  示例 的  simple 全归约 DDP 通信 钩子 that uses get_future API 到 retrieve  Future associated 使用  completion 的 全归约.

get_future API supports NCCL, 和 partially GLOO 和 MPI backends (no support 用于 peer-到-peer 操作 like send/recv) 和 将 return  torch.futures.Future.

在  示例 above, 全归约 work 将 是 done 在 GPU using NCCL backend, fut.wait() 将 return after synchronizing  appropriate NCCL streams 使用 PyTorch’s current 设备 streams 到 ensure we 可以 有 asynchronous CUDA execution 和 it 做 not wait 用于  entire 操作 到 complete 在 GPU. Note that CUDAFuture 做 not support TORCH_NCCL_BLOCKING_WAIT flag 或 NCCL’s barrier(). 在 addition, if  callback 函数 是 added 由 fut.then(), it 将 wait until WorkNCCL’s NCCL streams synchronize 使用 ProcessGroupNCCL’s dedicated callback stream 和 invoke  callback inline after running  callback 在  callback stream. fut.then() 将 return another CUDAFuture that holds  return value 的  callback 和  CUDAEvent that recorded  callback stream.

用于 CPU work, fut.done() returns true when work 有 是 completed 和 value() tensors 是 ready.

用于 GPU work, fut.done() returns true only whether  操作 有 是 enqueued.

用于 mixed CPU-GPU work (e.g. sending GPU tensors 使用 GLOO), fut.done() returns true when tensors 有 arrived 在 respective nodes, but not yet necessarily synched 在 respective GPUs (similarly 到 GPU work).

 torch.futures.Future object 的 int type which maps 到  enum type 的 WorkResult As  示例,  future object 可以 是 retrieved 由 fut = process_group.全归约(张量).get_future_result().

users 可以 use fut.wait() 到 blocking wait 用于  completion 的  work 和 get  WorkResult 由 fut.value(). Also, users 可以 use fut.then(call_back_func) 到 register  callback 函数 到 是 called when  work 是 completed, without blocking  current thread.

get_future_result API supports NCCL

在 normal cases, users 做 not need 到 set  timeout. calling wait() 是  same as calling synchronize(): Letting  current stream block 在  completion 的  NCCL work. However, if timeout 是 set, it 将 block  CPU thread until  NCCL work 是 completed 或 timed out. If timeout, exception 将 是 thrown.

 enum-like class 用于 available reduction 操作: SUM, PRODUCT, MIN, MAX, BAND, BOR, BXOR, 和 PREMUL_SUM.

BAND, BOR, 和 BXOR reductions 是 not available when using  NCCL backend.

AVG divides values 由  world size before summing across ranks. AVG 是 only available 使用  NCCL backend, 和 only 用于 NCCL versions 2.10 或 later.

PREMUL_SUM multiplies inputs 由  given scalar locally before reduction. PREMUL_SUM 是 only available 使用  NCCL backend, 和 only available 用于 NCCL versions 2.11 或 later. Users 是 supposed 到 use torch.分布式._make_nccl_premul_sum.

Additionally, MAX, MIN 和 PRODUCT 是 not supported 用于 complex tensors.

 values 的 this class 可以 是 accessed as attributes, e.g., ReduceOp.SUM. They 是 used 在 specifying strategies 用于 reduction collectives, e.g., 归约().

This class 做 not support __members__ property.

Deprecated enum-like class 用于 reduction 操作: SUM, PRODUCT, MIN, 和 MAX.

ReduceOp 是 recommended 到 use instead.

 分布式 package comes 使用  分布式 key-value store, which 可以 是 used 到 share information between 进程 在  group as well as 到 initialize  分布式 package 在 torch.分布式.init_process_group() (由 explicitly creating  store as  alternative 到 specifying init_method.) There 是 3 choices 用于 Key-Value Stores: TCPStore, FileStore, 和 HashStore.

Base class 用于 all store implementations, such as  3 provided 由 PyTorch 分布式: (TCPStore, FileStore, 和 HashStore).

 first call 到 add 用于  given key creates  counter associated 使用 key 在  store, initialized 到 amount. Subsequent calls 到 add 使用  same key increment  counter 由  specified amount. Calling add() 使用  key that 有 already 是 set 在  store 由 set() 将 result 在  exception.

key (str) –  key 在  store whose counter 将 是 incremented.

amount (int) –  quantity 由 which  counter 将 是 incremented.

Append  key-value pair into  store based 在  supplied key 和 value. If key 做 not exists 在  store, it 将 是 created.

key (str) –  key 到 是 appended 到  store.

value (str) –  value associated 使用 key 到 是 added 到  store.

 call 到 check whether  given list 的 keys 有 value stored 在  store. This call immediately returns 在 normal cases but still suffers 从 some edge deadlock cases, e.g, calling check after TCPStore 有 是 destroyed. Calling check() 使用  list 的 keys that one wants 到 check whether stored 在  store 或 not.

keys (list[str]) –  keys 到 query whether stored 在  store.

Clones  store 和 returns  new object that points 到  same underlying store.  returned store 可以 是 used concurrently 使用  original object. This 是 intended 到 provide  safe way 到 use  store 从 multiple threads 由 cloning one store per thread.

Inserts  key-value pair into  store based 在  supplied key 和 performs comparison between expected_value 和 desired_value before inserting. desired_value 将 only 是 set if expected_value 用于  key already exists 在  store 或 if expected_value 是  empty string.

key (str) –  key 到 是 checked 在  store.

expected_value (str) –  value associated 使用 key 到 是 checked before insertion.

desired_value (str) –  value associated 使用 key 到 是 added 到  store.

Deletes  key-value pair associated 使用 key 从  store. Returns true if  key 是 successfully deleted, 和 false if it 是 not.

 delete_key API 是 only supported 由  TCPStore 和 HashStore. Using this API 使用  FileStore 将 result 在  exception.

key (str) –  key 到 是 deleted 从  store

True if key 是 deleted, otherwise False.

Retrieves  value associated 使用  given key 在  store. If key 是 not present 在  store,  函数 将 wait 用于 timeout, which 是 defined when initializing  store, before throwing  exception.

key (str) –  函数 将 return  value associated 使用 this key.

Value associated 使用 key if key 是 在  store.

Returns true if  store supports extended 操作.

Retrieve all values 在 keys. If any key 在 keys 是 not present 在  store,  函数 将 wait 用于 timeout

keys (List[str]) –  keys 到 是 retrieved 从  store.

Inserts  list key-value pair into  store based 在  supplied keys 和 values

keys (List[str]) –  keys 到 insert.

values (List[str]) –  values 到 insert.

Returns  number 的 keys set 在  store. Note that this number 将 typically 是 one greater than  number 的 keys added 由 set() 和 add() since one key 是 used 到 coordinate all  workers using  store.

When used 使用  TCPStore, num_keys returns  number 的 keys written 到  underlying file. If  store 是 destructed 和 another store 是 created 使用  same file,  original keys 将 是 retained.

 number 的 keys present 在  store.

Returns  length 的  specified queue.

If  queue doesn’t exist it returns 0.

See queue_push 用于 more details.

key (str) –  key 的  queue 到 get  length.

Pops  value 从  specified queue 或 waits until timeout if  queue 是 empty.

See queue_push 用于 more details.

If block 是 False,  dist.QueueEmptyError 将 是 raised if  queue 是 empty.

key (str) –  key 的  queue 到 pop 从.

block (bool) – Whether 到 block waiting 用于  key 或 immediately return.

Pushes  value into  specified queue.

Using  same key 用于 queues 和 set/get 操作 可能 result 在 unexpected behavior.

wait/check 操作 是 supported 用于 queues.

wait 使用 queues 将 only wake one waiting worker rather than all.

key (str) –  key 的  queue 到 push 到.

value (str) –  value 到 push into  queue.

Inserts  key-value pair into  store based 在  supplied key 和 value. If key already exists 在  store, it 将 overwrite  old value 使用  new supplied value.

key (str) –  key 到 是 added 到  store.

value (str) –  value associated 使用 key 到 是 added 到  store.

Sets  store’s default timeout. This timeout 是 used during 初始化 和 在 wait() 和 get().

timeout (timedelta) – timeout 到 是 set 在  store.

Gets  timeout 的  store.

wait(self: torch._C._distributed_c10d.Store, arg0: collections.abc.序列[str]) -> None

Waits 用于 each key 在 keys 到 是 added 到  store. If not all keys 是 set before  timeout (set during store 初始化), then wait 将 throw  exception.

keys (list) – List 的 keys 在 which 到 wait until they 是 set 在  store.

wait(self: torch._C._distributed_c10d.Store, arg0: collections.abc.序列[str], arg1: datetime.timedelta) -> None

Waits 用于 each key 在 keys 到 是 added 到  store, 和 throws  exception if  keys 有 not 是 set 由  supplied timeout.

keys (list) – List 的 keys 在 which 到 wait until they 是 set 在  store.

timeout (timedelta) – Time 到 wait 用于  keys 到 是 added before throwing  exception.

 TCP-based 分布式 key-value store 实现.  server store holds  数据, while  client stores 可以 connect 到  server store over TCP 和 perform actions such as set() 到 insert  key-value pair, get() 到 retrieve  key-value pair, etc. There 应该 always 是 one server store initialized because  client store(s) 将 wait 用于  server 到 establish  connection.

host_name (str) –  hostname 或 IP Address  server store 应该 run 在.

port (int) –  port 在 which  server store 应该 listen 用于 incoming requests.

world_size (int, optional) –  total number 的 store users (number 的 clients + 1 用于  server). Default 是 None (None indicates  non-fixed number 的 store users).

is_master (bool, optional) – True when initializing  server store 和 False 用于 client stores. Default 是 False.

timeout (timedelta, optional) – Timeout used 由  store during 初始化 和 用于 methods such as get() 和 wait(). Default 是 timedelta(seconds=300)

wait_for_workers (bool, optional) – Whether 到 wait 用于 all  workers 到 connect 使用  server store. This 是 only applicable when world_size 是  fixed value. Default 是 True.

multi_tenant (bool, optional) – If True, all TCPStore instances 在  current 进程 使用  same host/port 将 use  same underlying TCPServer. Default 是 False.

master_listen_fd (int, optional) – If specified,  underlying TCPServer 将 listen 在 this file descriptor, which 必须 是  socket already bound 到 port. 到 bind  ephemeral port we recommend setting  port 到 0 和 reading .port. Default 是 None (meaning  server creates  new socket 和 attempts 到 bind it 到 port).

use_libuv (bool, optional) – If True, use libuv 用于 TCPServer backend. Default 是 True.

Creates  new TCPStore.

Gets  hostname 在 which  store listens 用于 requests.

Returns True if it’s using  libuv backend.

Gets  port number 在 which  store listens 用于 requests.

 thread-safe store 实现 based 在  underlying hashmap. This store 可以 是 used within  same 进程 (用于 示例, 由 other threads), but cannot 是 used across 进程.

Creates  new HashStore.

 store 实现 that uses  file 到 store  underlying key-value pairs.

file_name (str) – path 的  file 在 which 到 store  key-value pairs

world_size (int, optional) –  total number 的 进程 using  store. Default 是 -1 ( negative value indicates  non-fixed number 的 store users).

Creates  new FileStore.

Gets  path 的  file used 由 FileStore 到 store key-value pairs.

 wrapper around any 的  3 key-value stores (TCPStore, FileStore, 和 HashStore) that adds  prefix 到 each key inserted 到  store.

prefix (str) –  prefix string that 是 prepended 到 each key before 正在 inserted into  store.

store (torch.分布式.store) –  store object that forms  underlying key-value store.

Creates  new PrefixStore.

Gets  underlying store object that PrefixStore wraps around.

Note that you 可以 use torch.profiler (recommended, only available after 1.8.1) 或 torch.自动求导.profiler 到 profile collective 通信 和 point-到-point 通信 APIs mentioned here. All out-的--box backends (gloo, nccl, mpi) 是 supported 和 collective 通信 usage 将 是 rendered as expected 在 profiling 输出/traces. Profiling your code 是  same as any regular torch operator:

Please refer 到  profiler documentation 用于  full overview 的 profiler features.

 multi-GPU functions (which stand 用于 multiple GPUs per CPU thread) 是 deprecated. As 的 today, PyTorch 分布式’s preferred programming 模型 是 one 设备 per thread, as exemplified 由  APIs 在 this document. If you 是  backend developer 和 want 到 support multiple 设备 per thread, please contact PyTorch 分布式’s maintainers.

Object collectives 有  number 的 serious limitations. Read further 到 determine if they 是 safe 到 use 用于 your use case.

Object collectives 是  set 的 collective-like 操作 that work 在 arbitrary Python objects, as long as they 可以 是 pickled. There 是 various collective patterns implemented (e.g. 广播, all_gather, …) but they each roughly follow this pattern:

convert  输入 object into  pickle (raw bytes), then shove it into  byte 张量

communicate  size 的 this byte 张量 到 peers (first collective 操作)

allocate appropriately sized 张量 到 perform  real collective

communicate  object 数据 (second collective 操作)

convert raw 数据 back into Python (unpickle)

Object collectives sometimes 有 surprising 性能 或 内存 characteristics that lead 到 long runtimes 或 OOMs, 和 thus they 应该 是 used 使用 caution. Here 是 some common issues.

Asymmetric pickle/unpickle time - Pickling objects 可以 是 slow, depending 在  number, type 和 size 的  objects. When  collective 有  fan-在 (e.g. gather_object),  receiving rank(s) 必须 unpickle N times more objects than  sending rank(s) 有 到 pickle, which 可以 cause other ranks 到 time out 在 their next collective.

Inefficient 张量 通信 - Tensors 应该 是 sent via regular collective APIs, not object collective APIs. It 是 possible 到 send Tensors via object collective APIs, but they 将 是 serialized 和 deserialized (including  CPU-sync 和 设备-到-host copy 在  case 的 non-CPU tensors), 和 在 almost every case other than debugging 或 troubleshooting code, it 将 是 worth  trouble 到 refactor  code 到 use non-object collectives instead.

Unexpected 张量 设备 - If you still want 到 send tensors via object collectives, there 是 another aspect specific 到 cuda (和 possibly other accelerators) tensors. If you pickle  张量 that 是 currently 在 cuda:3, 和 then unpickle it, you 将 get another 张量 在 cuda:3 regardless 的 which 进程 you 是 在, 或 which CUDA 设备 是  ‘default’ 设备 用于 that 进程. 使用 regular 张量 collective APIs, ‘输出 tensors’ 将 always 是 在  same, local 设备, which 是 generally what you’d expect.

Unpickling  张量 将 implicitly activate  CUDA context if it 是  first time  GPU 是 used 由  进程, which 可以 waste significant amounts 的 GPU 内存. This issue 可以 是 avoided 由 moving tensors 到 CPU before passing them as inputs 到  object collective.

Besides  builtin GLOO/MPI/NCCL backends, PyTorch 分布式 supports third-party backends through  run-time register mechanism. 用于 references 在 how 到 develop  third-party backend through C++ Extension, please refer 到 Tutorials - Custom C++ 和 CUDA Extensions 和 test/cpp_extensions/cpp_c10d_extension.cpp.  capability 的 third-party backends 是 decided 由 their own implementations.

 new backend derives 从 c10d::进程组 和 registers  backend name 和  instantiating 接口 through torch.分布式.Backend.register_backend() when imported.

When manually importing this backend 和 invoking torch.分布式.init_process_group() 使用  corresponding backend name,  torch.分布式 package runs 在  new backend.

 support 的 third-party backend 是 experimental 和 subject 到 change.

 torch.分布式 package also provides  launch utility 在 torch.分布式.launch. This helper utility 可以 是 used 到 launch multiple 进程 per node 用于 分布式 训练.

模块 torch.分布式.launch.

torch.分布式.launch 是  模块 that spawns up multiple 分布式 训练 进程 在 each 的  训练 nodes.

This 模块 是 going 到 是 deprecated 在 favor 的 torchrun.

 utility 可以 是 used 用于 single-node 分布式 训练, 在 which one 或 more 进程 per node 将 是 spawned.  utility 可以 是 used 用于 either CPU 训练 或 GPU 训练. If  utility 是 used 用于 GPU 训练, each 分布式 进程 将 是 operating 在  single GPU. This 可以 achieve well-improved single-node 训练 性能. It 可以 also 是 used 在 multi-node 分布式 训练, 由 spawning up multiple 进程 在 each node 用于 well-improved multi-node 分布式 训练 性能 as well. This 将 especially 是 beneficial 用于 systems 使用 multiple Infiniband interfaces that 有 direct-GPU support, since all 的 them 可以 是 utilized 用于 aggregated 通信 bandwidth.

在 both cases 的 single-node 分布式 训练 或 multi-node 分布式 训练, this utility 将 launch  given number 的 进程 per node (--nproc-per-node). If used 用于 GPU 训练, this number needs 到 是 less 或 equal 到  number 的 GPUs 在  current system (nproc_per_node), 和 each 进程 将 是 operating 在  single GPU 从 GPU 0 到 GPU (nproc_per_node - 1).

How 到 use this 模块:

Single-Node multi-进程 分布式 训练

Multi-Node multi-进程 分布式 训练: (e.g. two nodes)

Node 1: (IP: 192.168.1.1, 和 有  free port: 1234)

到 look up what optional arguments this 模块 offers:

1. This utility 和 multi-进程 分布式 (single-node 或 multi-node) GPU 训练 currently only achieves  best 性能 using  NCCL 分布式 backend. Thus NCCL backend 是  recommended backend 到 use 用于 GPU 训练.

2. 在 your 训练 program, you 必须 parse  command-line argument: --local-rank=LOCAL_PROCESS_RANK, which 将 是 provided 由 this 模块. If your 训练 program uses GPUs, you 应该 ensure that your code only runs 在  GPU 设备 的 LOCAL_PROCESS_RANK. This 可以 是 done 由:

Parsing  local_rank argument

Set your 设备 到 local rank using either

Changed 在 version 2.0.0:  launcher 将 passes  --local-rank=<rank> argument 到 your script. 从 PyTorch 2.0.0 onwards,  dashed --local-rank 是 preferred over  previously used underscored --local_rank.

用于 backward compatibility, it 可能 是 necessary 用于 users 到 handle both cases 在 their argument parsing code. This means including both "--local-rank" 和 "--local_rank" 在  argument parser. If only "--local_rank" 是 provided,  launcher 将 trigger  error: “error: unrecognized arguments: –local-rank=<rank>”. 用于 训练 code that only supports PyTorch 2.0.0+, including "--local-rank" 应该 是 sufficient.

3. 在 your 训练 program, you 是 supposed 到 call  following 函数 at  beginning 到 start  分布式 backend. It 是 strongly recommended that init_method=env://. Other init methods (e.g. tcp://) 可能 work, but env:// 是  one that 是 officially supported 由 this 模块.

4. 在 your 训练 program, you 可以 either use regular 分布式 functions 或 use torch.nn.并行.分布式数据并行() 模块. If your 训练 program uses GPUs 用于 训练 和 you 将 like 到 use torch.nn.并行.分布式数据并行() 模块, here 是 how 到 configure it.

Please ensure that device_ids argument 是 set 到 是  only GPU 设备 id that your code 将 是 operating 在. This 是 generally  local rank 的  进程. 在 other words,  device_ids needs 到 是 [args.local_rank], 和 output_device needs 到 是 args.local_rank 在 order 到 use this utility

5. Another way 到 传播 local_rank 到  subprocesses via environment variable LOCAL_RANK. This behavior 是 enabled when you launch  script 使用 --use-env=True. You 必须 adjust  subprocess 示例 above 到 replace args.local_rank 使用 os.environ['LOCAL_RANK'];  launcher 将 not 传播 --local-rank when you specify this flag.

local_rank 是 NOT globally unique: it 是 only unique per 进程 在  machine. Thus, don’t use it 到 decide if you 应该, e.g., write 到  networked filesystem. See pytorch/pytorch#12042 用于  示例 的 how things 可以 go wrong if you don’t 做 this correctly.

 Multiprocessing package - torch.multiprocessing package also provides  spawn 函数 在 torch.multiprocessing.spawn(). This helper 函数 可以 是 used 到 spawn multiple 进程. It works 由 passing 在  函数 that you want 到 run 和 spawns N 进程 到 run it. This 可以 是 used 用于 multiprocess 分布式 训练 as well.

用于 references 在 how 到 use it, please refer 到 PyTorch 示例 - ImageNet 实现

Note that this 函数 requires Python 3.4 或 higher.

Debugging 分布式 applications 可以 是 challenging due 到 hard 到 understand hangs, crashes, 或 inconsistent behavior across ranks. torch.分布式 provides  suite 的 tools 到 help debug 训练 applications 在  self-serve fashion:

It 是 extremely convenient 到 use python’s debugger 在  分布式 environment, but because it 做 not work out 的  box many people 做 not use it at all. PyTorch offers  customized wrapper around pdb that streamlines  进程.

torch.分布式.breakpoint makes this 进程 easy. Internally, it customizes pdb’s breakpoint behavior 在 two ways but otherwise behaves as normal pdb.

Attaches  debugger only 在 one rank (specified 由  user).

Ensures all other ranks stop, 由 using  torch.分布式.barrier() that 将 release once  debugged rank issues  continue

Reroutes stdin 从  child 进程 such that it connects 到 your terminal.

到 use it, simply issue torch.分布式.breakpoint(rank) 在 all ranks, using  same value 用于 rank 在 each case.

As 的 v1.10, torch.分布式.monitored_barrier() exists as  alternative 到 torch.分布式.barrier() which fails 使用 helpful information about which rank 可能 是 faulty when crashing, i.e. not all ranks calling into torch.分布式.monitored_barrier() within  provided timeout. torch.分布式.monitored_barrier() implements  host-side barrier using send/recv 通信 primitives 在  进程 similar 到 acknowledgements, allowing rank 0 到 report which rank(s) failed 到 acknowledge  barrier 在 time. As  示例, consider  following 函数 where rank 1 fails 到 call into torch.分布式.monitored_barrier() (在 practice this 可以 是 due 到  application bug 或 hang 在  previous collective):

 following error message 是 produced 在 rank 0, allowing  user 到 determine which rank(s) 可能 是 faulty 和 investigate further:

使用 TORCH_CPP_LOG_LEVEL=INFO,  environment variable TORCH_DISTRIBUTED_DEBUG 可以 是 used 到 trigger additional useful logging 和 collective 同步 checks 到 ensure all ranks 是 synchronized appropriately. TORCH_DISTRIBUTED_DEBUG 可以 是 set 到 either OFF (default), INFO, 或 DETAIL depending 在  debugging level required. Please note that  most verbose option, DETAIL 可能 impact  application 性能 和 thus 应该 only 是 used when debugging issues.

Setting TORCH_DISTRIBUTED_DEBUG=INFO 将 result 在 additional debug logging when 模型 trained 使用 torch.nn.并行.分布式数据并行() 是 initialized, 和 TORCH_DISTRIBUTED_DEBUG=DETAIL 将 additionally log runtime 性能 statistics  select number 的 iterations. These runtime statistics include 数据 such as forward time, backward time, 梯度 通信 time, etc. As  示例, given  following application:

 following logs 是 rendered at 初始化 time:

 following logs 是 rendered during runtime (when TORCH_DISTRIBUTED_DEBUG=DETAIL 是 set):

在 addition, TORCH_DISTRIBUTED_DEBUG=INFO enhances crash logging 在 torch.nn.并行.分布式数据并行() due 到 unused 参数 在  模型. Currently, find_unused_parameters=True 必须 是 passed into torch.nn.并行.分布式数据并行() 初始化 if there 是 参数 that 可能 是 unused 在  前向传播, 和 as 的 v1.10, all 模型 outputs 是 required 到 是 used 在 损失 计算 as torch.nn.并行.分布式数据并行() 做 not support unused 参数 在  backwards 传播. These constraints 是 challenging especially 用于 larger 模型, thus when crashing 使用  error, torch.nn.并行.分布式数据并行() 将 log  fully qualified name 的 all 参数 that went unused. 用于 示例, 在  above application, if we modify 损失 到 是 instead computed as 损失 = 输出[1], then TwoLinLayerNet. 做 not receive  梯度 在  backwards 传播, 和 thus results 在 DDP failing. 在  crash,  user 是 passed information about 参数 which went unused, which 可能 是 challenging 到 manually find 用于 large 模型:

Setting TORCH_DISTRIBUTED_DEBUG=DETAIL 将 trigger additional consistency 和 同步 checks 在 every collective call issued 由  user either directly 或 indirectly (such as DDP 全归约). This 是 done 由 creating  wrapper 进程 group that wraps all 进程 groups returned 由 torch.分布式.init_process_group() 和 torch.分布式.new_group() APIs. As  result, these APIs 将 return  wrapper 进程 group that 可以 是 used exactly like  regular 进程 group, but performs consistency checks before dispatching  collective 到  underlying 进程 group. Currently, these checks include  torch.分布式.monitored_barrier(), which ensures all ranks complete their outstanding collective calls 和 reports ranks which 是 stuck. Next,  collective itself 是 checked 用于 consistency 由 ensuring all collective functions match 和 是 called 使用 consistent 张量 shapes. If this 是 not  case,  detailed error report 是 included when  application crashes, rather than  hang 或 uninformative error message. As  示例, consider  following 函数 which 有 mismatched 输入 shapes into torch.分布式.all_reduce():

使用  NCCL backend, such  application 将 likely result 在  hang which 可以 是 challenging 到 root-cause 在 nontrivial scenarios. If  user enables TORCH_DISTRIBUTED_DEBUG=DETAIL 和 reruns  application,  following error message reveals  root cause:

用于 fine-grained control 的  debug level during runtime  functions torch.分布式.set_debug_level(), torch.分布式.set_debug_level_from_env(), 和 torch.分布式.get_debug_level() 可以 also 是 used.

在 addition, TORCH_DISTRIBUTED_DEBUG=DETAIL 可以 是 used 在 conjunction 使用 TORCH_SHOW_CPP_STACKTRACES=1 到 log  entire callstack when  collective desynchronization 是 detected. These collective desynchronization checks 将 work 用于 all applications that use c10d collective calls backed 由 进程 groups created 使用  torch.分布式.init_process_group() 和 torch.分布式.new_group() APIs.

在 addition 到 explicit debugging support via torch.分布式.monitored_barrier() 和 TORCH_DISTRIBUTED_DEBUG,  underlying C++ library 的 torch.分布式 also outputs log messages at various levels. These messages 可以 是 helpful 到 understand  execution 状态 的  分布式 训练 job 和 到 troubleshoot problems such as 网络 connection failures.  following matrix shows how  log level 可以 是 adjusted via  combination 的 TORCH_CPP_LOG_LEVEL 和 TORCH_DISTRIBUTED_DEBUG environment variables.

TORCH_DISTRIBUTED_DEBUG

分布式 components raise custom Exception types derived 从 RuntimeError:

torch.分布式.DistError: This 是  base type 的 all 分布式 exceptions.

torch.分布式.DistBackendError: This exception 是 thrown when  backend-specific error occurs. 用于 示例, if  NCCL backend 是 used 和  user attempts 到 use  GPU that 是 not available 到  NCCL library.

torch.分布式.DistNetworkError: This exception 是 thrown when networking libraries encounter errors (ex: Connection reset 由 peer)

torch.分布式.DistStoreError: This exception 是 thrown when  Store encounters  error (ex: TCPStore timeout)

Exception raised when  error occurs 在  分布式 library

Exception raised when  backend error occurs 在 分布式

Exception raised when  网络 error occurs 在 分布式

Exception raised when  error occurs 在  分布式 store

If you 是 running single node 训练, it 可能 是 convenient 到 interactively breakpoint your script. We offer  way 到 conveniently breakpoint  single rank:

Set  breakpoint, but only 在  single rank. All other ranks 将 wait 用于 you 到 是 done 使用  breakpoint before continuing.

rank (int) – Which rank 到 break 在. Default: 0

skip (int) – Skip  first skip calls 到 this breakpoint. Default: 0.

---

## 分布式数据并行#

**URL:** https://pytorch.org/docs/stable/generated/torch.nn.并行.分布式数据并行.html

**Contents:**
- 分布式数据并行#

Implement 分布式 数据 parallelism based 在 torch.分布式 at 模块 level.

This container provides 数据 parallelism 由 synchronizing 梯度 across each 模型 副本.  设备 到 synchronize across 是 specified 由  输入 process_group, which 是  entire world 由 default. Note that 分布式数据并行 做 not chunk 或 otherwise shard  输入 across participating GPUs;  user 是 responsible 用于 defining how 到 做 so, 用于 示例 through  use 的  DistributedSampler.

See also: Basics 和 Use nn.并行.分布式数据并行 instead 的 multiprocessing 或 nn.DataParallel.  same constraints 在 输入 as 在 torch.nn.DataParallel apply.

Creation 的 this class requires that torch.分布式 到 是 already initialized, 由 calling torch.分布式.init_process_group().

分布式数据并行 是 proven 到 是 significantly faster than torch.nn.DataParallel 用于 single-node multi-GPU 数据 并行 训练.

到 use 分布式数据并行 在  host 使用 N GPUs, you 应该 spawn up N 进程, ensuring that each 进程 exclusively works 在  single GPU 从 0 到 N-1. This 可以 是 done 由 either setting CUDA_VISIBLE_DEVICES 用于 every 进程 或 由 calling  following API 用于 GPUs,

或 calling  unified API 用于 accelerator,

where i 是 从 0 到 N-1. 在 each 进程, you 应该 refer  following 到 construct this 模块:

或 you 可以 use  latest API 用于 初始化:

在 order 到 spawn up multiple 进程 per node, you 可以 use either torch.分布式.launch 或 torch.multiprocessing.spawn.

Please refer 到 PyTorch 分布式 Overview 用于  brief introduction 到 all features related 到 分布式 训练.

分布式数据并行 可以 是 used 在 conjunction 使用 torch.分布式.optim.ZeroRedundancyOptimizer 到 归约 per-rank 优化器 states 内存 footprint. Please refer 到 ZeroRedundancyOptimizer recipe 用于 more details.

nccl backend 是 currently  fastest 和 highly recommended backend when using GPUs. This applies 到 both single-node 和 multi-node 分布式 训练.

This 模块 also supports mixed-precision 分布式 训练. This means that your 模型 可以 有 different types 的 参数 such as mixed types 的 fp16 和 fp32,  梯度 reduction 在 these mixed types 的 参数 将 just work fine.

If you use torch.save 在 one 进程 到 checkpoint  模块, 和 torch.load 在 some other 进程 到 recover it, make sure that map_location 是 configured properly 用于 every 进程. Without map_location, torch.load 将 recover  模块 到 设备 where  模块 是 saved 从.

When  模型 是 trained 在 M nodes 使用 批次=N,  梯度 将 是 M times smaller when compared 到  same 模型 trained 在  single node 使用 批次=M*N if  损失 是 summed (NOT averaged as usual) across instances 在  批次 (because  梯度 between different nodes 是 averaged). You 应该 take this into consideration when you want 到 obtain  mathematically equivalent 训练 进程 compared 到  local 训练 counterpart. But 在 most cases, you 可以 just treat  分布式数据并行 wrapped 模型,  DataParallel wrapped 模型 和  ordinary 模型 在  single GPU as  same (E.g. using  same 学习率 用于 equivalent 批次 size).

参数 是 never 广播 between 进程.  模块 performs  all-归约 步骤 在 梯度 和 assumes that they 将 是 modified 由  优化器 在 all 进程 在  same way. Buffers (e.g. BatchNorm stats) 是 广播 从  模块 在 进程 的 rank 0, 到 all other 副本 在  system 在 every 迭代.

If you 是 using 分布式数据并行 在 conjunction 使用  分布式 RPC Framework, you 应该 always use torch.分布式.自动求导.backward() 到 compute 梯度 和 torch.分布式.optim.DistributedOptimizer 用于 optimizing 参数.

分布式数据并行 currently offers limited support 用于 梯度 checkpointing 使用 torch.utils.checkpoint(). If  checkpoint 是 done 使用 use_reentrant=False (recommended), DDP 将 work as expected without any limitations. If, however,  checkpoint 是 done 使用 use_reentrant=True ( default), DDP 将 work as expected when there 是 no unused 参数 在  模型 和 each 层 是 checkpointed at most once (make sure you 是 not passing find_unused_parameters=True 到 DDP). We currently 做 not support  case where  层 是 checkpointed multiple times, 或 when there unused 参数 在  checkpointed 模型.

到 let  non-DDP 模型 load  状态 dict 从  DDP 模型, consume_prefix_in_state_dict_if_present() needs 到 是 applied 到 strip  prefix “模块.” 在  DDP 状态 dict before loading.

Constructor, forward method, 和 differentiation 的  输出 (或  函数 的  输出 的 this 模块) 是 分布式 同步 points. Take that into account 在 case different 进程 可能 是 executing different code.

This 模块 assumes all 参数 是 registered 在  模型 由  time it 是 created. No 参数 应该 是 added nor removed later. Same applies 到 buffers.

This 模块 assumes all 参数 是 registered 在  模型 的 each 分布式 进程 是 在  same order.  模块 itself 将 conduct 梯度 全归约 following  reverse order 的  registered 参数 的  模型. 在 other words, it 是 users’ responsibility 到 ensure that each 分布式 进程 有  exact same 模型 和 thus  exact same 参数 registration order.

This 模块 allows 参数 使用 non-rowmajor-contiguous strides. 用于 示例, your 模型 可能 contain some 参数 whose torch.memory_format 是 torch.contiguous_format 和 others whose format 是 torch.channels_last. However, corresponding 参数 在 different 进程 必须 有  same strides.

This 模块 doesn’t work 使用 torch.自动求导.grad() (i.e. it 将 only work if 梯度 是 到 是 accumulated 在 .grad attributes 的 参数).

If you plan 在 using this 模块 使用  nccl backend 或  gloo backend (that uses Infiniband), together 使用  DataLoader that uses multiple workers, please change  multiprocessing start method 到 forkserver (Python 3 only) 或 spawn. Unfortunately Gloo (that uses Infiniband) 和 NCCL2 是 not fork safe, 和 you 将 likely experience deadlocks if you don’t change this setting.

You 应该 never try 到 change your 模型’s 参数 after wrapping up your 模型 使用 分布式数据并行. Because, when wrapping up your 模型 使用 分布式数据并行,  constructor 的 分布式数据并行 将 register  additional 梯度 reduction functions 在 all  参数 的  模型 itself at  time 的 构造. If you change  模型’s 参数 afterwards, 梯度 reduction functions no longer match  correct set 的 参数.

Using 分布式数据并行 在 conjunction 使用  分布式 RPC Framework 是 experimental 和 subject 到 change.

模块 (模块) – 模块 到 是 parallelized

device_ids (list 的 int 或 torch.设备) – CUDA 设备. 1) 用于 single-设备 modules, device_ids 可以 contain exactly one 设备 id, which represents  only CUDA 设备 where  输入 模块 corresponding 到 this 进程 resides. Alternatively, device_ids 可以 also 是 None. 2) 用于 multi-设备 modules 和 CPU modules, device_ids 必须 是 None. When device_ids 是 None 用于 both cases, both  输入 数据 用于  前向传播 和  actual 模块 必须 是 placed 在  correct 设备. (default: None)

CUDA 设备. 1) 用于 single-设备 modules, device_ids 可以 contain exactly one 设备 id, which represents  only CUDA 设备 where  输入 模块 corresponding 到 this 进程 resides. Alternatively, device_ids 可以 also 是 None. 2) 用于 multi-设备 modules 和 CPU modules, device_ids 必须 是 None.

When device_ids 是 None 用于 both cases, both  输入 数据 用于  前向传播 和  actual 模块 必须 是 placed 在  correct 设备. (default: None)

output_device (int 或 torch.设备) – 设备 location 的 输出 用于 single-设备 CUDA modules. 用于 multi-设备 modules 和 CPU modules, it 必须 是 None, 和  模块 itself dictates  输出 location. (default: device_ids[0] 用于 single-设备 modules)

broadcast_buffers (bool) – Flag that enables syncing (broadcasting) buffers 的  模块 at beginning 的  forward 函数. (default: True)

init_sync (bool) – Whether 到 sync during 初始化 到 verify param shapes 和 广播 参数 和 buffers. WARNING: if this 是 set 到 False  user 是 required 到 ensure themselves that  weights 是  same 在 all ranks. (default: True)

process_group –  进程 group 到 是 used 用于 分布式 数据 all-reduction. If None,  default 进程 group, which 是 created 由 torch.分布式.init_process_group(), 将 是 used. (default: None)

bucket_cap_mb – 分布式数据并行 将 桶 参数 into multiple 桶 so that 梯度 reduction 的 each 桶 可以 potentially 重叠 使用 backward 计算. bucket_cap_mb controls  桶 size 在 MebiBytes (MiB). If None,  default size 的 25 MiB 将 是 used. (default: None)

find_unused_parameters (bool) – Traverse  自动求导 图 从 all tensors contained 在  return value 的  wrapped 模块’s forward 函数. 参数 that don’t receive 梯度 as part 的 this 图 是 preemptively marked as 正在 ready 到 是 reduced. 在 addition, 参数 that 可能 有 是 used 在  wrapped 模块’s forward 函数 but 是 not part 的 损失 计算 和 thus 将 also not receive 梯度 是 preemptively marked as ready 到 是 reduced. (default: False)

check_reduction – This argument 是 deprecated.

gradient_as_bucket_view (bool) – When set 到 True, 梯度 将 是 views pointing 到 different offsets 的 全归约 通信 桶. This 可以 归约 peak 内存 usage, where  saved 内存 size 将 是 equal 到  total 梯度 size. Moreover, it avoids  overhead 的 copying between 梯度 和 全归约 通信 桶. When 梯度 是 views, detach_() cannot 是 called 在  梯度. If hitting such errors, please fix it 由 referring 到  zero_grad() 函数 在 torch/optim/优化器.py as  solution. Note that 梯度 将 是 views after first 迭代, so  peak 内存 saving 应该 是 checked after first 迭代.

static_graph (bool) – When set 到 True, DDP knows  trained 图 是 static. Static 图 means 1)  set 的 used 和 unused 参数 将 not change during  whole 训练 loop; 在 this case, it 做 not matter whether users set find_unused_parameters = True 或 not. 2) How  图 是 trained 将 not change during  whole 训练 loop (meaning there 是 no control flow depending 在 iterations). When static_graph 是 set 到 是 True, DDP 将 support cases that 可以 not 是 supported 在  past: 1) Reentrant backwards. 2) 激活 checkpointing multiple times. 3) 激活 checkpointing when 模型 有 unused 参数. 4) There 是 模型 参数 that 是 outside 的 forward 函数. 5) Potentially improve 性能 when there 是 unused 参数, as DDP 将 not search 图 在 each 迭代 到 detect unused 参数 when static_graph 是 set 到 是 True. 到 check whether you 可以 set static_graph 到 是 True, one way 是 到 check ddp logging 数据 at  end 的 your previous 模型 训练, if ddp_logging_data.get("can_set_static_graph") == True, mostly you 可以 set static_graph = True as well. 示例::>>> model_DDP = torch.nn.并行.分布式数据并行(模型) >>> # 训练 loop >>> ... >>> ddp_logging_data = model_DDP._get_ddp_logging_data() >>> static_graph = ddp_logging_data.get("can_set_static_graph")

When set 到 True, DDP knows  trained 图 是 static. Static 图 means 1)  set 的 used 和 unused 参数 将 not change during  whole 训练 loop; 在 this case, it 做 not matter whether users set find_unused_parameters = True 或 not. 2) How  图 是 trained 将 not change during  whole 训练 loop (meaning there 是 no control flow depending 在 iterations). When static_graph 是 set 到 是 True, DDP 将 support cases that 可以 not 是 supported 在  past: 1) Reentrant backwards. 2) 激活 checkpointing multiple times. 3) 激活 checkpointing when 模型 有 unused 参数. 4) There 是 模型 参数 that 是 outside 的 forward 函数. 5) Potentially improve 性能 when there 是 unused 参数, as DDP 将 not search 图 在 each 迭代 到 detect unused 参数 when static_graph 是 set 到 是 True. 到 check whether you 可以 set static_graph 到 是 True, one way 是 到 check ddp logging 数据 at  end 的 your previous 模型 训练, if ddp_logging_data.get("can_set_static_graph") == True, mostly you 可以 set static_graph = True as well.

delay_all_reduce_named_params (list 的 tuple 的 str 和 torch.nn.参数) –  list 的 named 参数 whose all 归约 将 是 delayed when  梯度 的  参数 specified 在 param_to_hook_all_reduce 是 ready. Other arguments 的 DDP 做 not apply 到 named params specified 在 this argument as these named params 将 是 ignored 由 DDP reducer.

param_to_hook_all_reduce (torch.nn.参数) –  参数 到 钩子 delayed all 归约 的 参数 specified 在 delay_all_reduce_named_params.

skip_all_reduce_unused_params – When set 到 True, DDP 将 skip reducing unused 参数. This requires that unused 参数 remain  same across all ranks throughout  entire 训练 进程. If this condition 是 not met, it 可能 cause desynchronization 和 result 在 训练 hang.

模块 (模块) –  模块 到 是 parallelized.

Context manager 用于 训练 使用 uneven inputs across 进程 在 DDP.

This context manager 将 keep track 的 already-joined DDP 进程, 和 “shadow”  forward 和 backward passes 由 inserting collective 通信 操作 到 match 使用  ones created 由 non-joined DDP 进程. This 将 ensure each collective call 有  corresponding call 由 already-joined DDP 进程, preventing hangs 或 errors that 将 otherwise happen when 训练 使用 uneven inputs across 进程. Alternatively, if  flag throw_on_early_termination 是 specified 到 是 True, all trainers 将 throw  error once one rank runs out 的 inputs, allowing these errors 到 是 caught 和 handled according 到 application logic.

Once all DDP 进程 有 joined,  context manager 将 广播  模型 corresponding 到  last joined 进程 到 all 进程 到 ensure  模型 是  same across all 进程 (which 是 guaranteed 由 DDP).

到 use this 到 enable 训练 使用 uneven inputs across 进程, simply wrap this context manager around your 训练 loop. No further modifications 到  模型 或 数据 loading 是 required.

If  模型 或 训练 loop this context manager 是 wrapped around 有 additional 分布式 collective 操作, such as SyncBatchNorm 在  模型’s 前向传播, then  flag throw_on_early_termination 必须 是 enabled. This 是 because this context manager 是 not aware 的 non-DDP collective 通信. This flag 将 cause all ranks 到 throw when any one rank exhausts inputs, allowing these errors 到 是 caught 和 recovered 从 across all ranks.

divide_by_initial_world_size (bool) – If True, 将 divide 梯度 由  initial world_size DDP 训练 是 launched 使用. If False, 将 compute  effective world size (number 的 ranks that 有 not depleted their inputs yet) 和 divide 梯度 由 that during 全归约. Set divide_by_initial_world_size=True 到 ensure every 输入 sample including  uneven inputs 有 equal 权重 在 terms 的 how much they contribute 到  global 梯度. This 是 achieved 由 always dividing  梯度 由  initial world_size even when we encounter uneven inputs. If you set this 到 False, we divide  梯度 由  remaining number 的 nodes. This ensures parity 使用 训练 在  smaller world_size although it also means  uneven inputs 将 contribute more towards  global 梯度. Typically, you 将 want 到 set this 到 True 用于 cases where  last few inputs 的 your 训练 job 是 uneven. 在 extreme cases, where there 是  large discrepancy 在  number 的 inputs, setting this 到 False 可能 provide better results.

enable (bool) – Whether 到 enable uneven 输入 detection 或 not. 传播 在 enable=False 到 disable 在 cases where you know that inputs 是 even across participating 进程. Default 是 True.

throw_on_early_termination (bool) – Whether 到 throw  error 或 continue 训练 when at least one rank 有 exhausted inputs. If True, 将 throw upon  first rank reaching end 的 数据. If False, 将 continue 训练 使用  smaller effective world size until all ranks 是 joined. Note that if this flag 是 specified, then  flag divide_by_initial_world_size 将 是 ignored. Default 是 False.

DDP join 钩子 enables 训练 在 uneven inputs 由 mirroring communications 在 forward 和 backward passes.

kwargs (dict) –  dict containing any keyword arguments 到 modify  behavior 的  join 钩子 at run time; all Joinable instances sharing  same join context manager 是 forwarded  same value 用于 kwargs.

If True, then 梯度 是 divided 由  initial world size that DDP 是 launched 使用. If False, then 梯度 是 divided 由  effective world size (i.e.  number 的 non-joined 进程), meaning that  uneven inputs contribute more toward  global 梯度. Typically, this 应该 是 set 到 True if  degree 的 unevenness 是 small but 可以 是 set 到 False 在 extreme cases 用于 possibly better results. Default 是 True.

Context manager 到 disable 梯度 synchronizations across DDP 进程.

Within this context, 梯度 将 是 accumulated 在 模块 variables, which 将 later 是 synchronized 在  first forward-反向传播 exiting  context.

 前向传播 应该 是 included inside  context manager, 或 else 梯度 将 still 是 synchronized.

Register 通信 钩子 用于 user-defined DDP aggregation 的 梯度 across multiple workers.

This 钩子 将 是 very useful 用于 researchers 到 try out new ideas. 用于 示例, this 钩子 可以 是 used 到 implement several algorithms like GossipGrad 和 梯度 compression which involve different 通信 strategies 用于 参数 syncs while running 分布式 DataParallel 训练.

状态 (object) – Passed 到  钩子 到 maintain any 状态 information during  训练 进程. Examples include error feedback 在 梯度 compression, peers 到 communicate 使用 next 在 GossipGrad, etc. It 是 locally stored 由 each worker 和 shared 由 all  梯度 tensors 在  worker.

Passed 到  钩子 到 maintain any 状态 information during  训练 进程. Examples include error feedback 在 梯度 compression, peers 到 communicate 使用 next 在 GossipGrad, etc.

It 是 locally stored 由 each worker 和 shared 由 all  梯度 tensors 在  worker.

钩子 (Callable) – Callable 使用  following signature: 钩子(状态: object, 桶: dist.GradBucket) -> torch.futures.Future[torch.张量]: This 函数 是 called once  桶 是 ready.  钩子 可以 perform whatever processing 是 needed 和 return  Future indicating completion 的 any async work (ex: 全归约). If  钩子 doesn’t perform any 通信, it still 必须 return  completed Future.  Future 应该 hold  new value 的 grad 桶’s tensors. Once  桶 是 ready, c10d reducer 将 call this 钩子 和 use  tensors returned 由  Future 和 copy grads 到 individual 参数. Note that  future’s return type 必须 是  single 张量. We also provide  API called get_future 到 retrieve  Future associated 使用  completion 的 c10d.进程组.Work. get_future 是 currently supported 用于 NCCL 和 also supported 用于 most 操作 在 GLOO 和 MPI, except 用于 peer 到 peer 操作 (send/recv).

Callable 使用  following signature: 钩子(状态: object, 桶: dist.GradBucket) -> torch.futures.Future[torch.张量]:

This 函数 是 called once  桶 是 ready.  钩子 可以 perform whatever processing 是 needed 和 return  Future indicating completion 的 any async work (ex: 全归约). If  钩子 doesn’t perform any 通信, it still 必须 return  completed Future.  Future 应该 hold  new value 的 grad 桶’s tensors. Once  桶 是 ready, c10d reducer 将 call this 钩子 和 use  tensors returned 由  Future 和 copy grads 到 individual 参数. Note that  future’s return type 必须 是  single 张量.

We also provide  API called get_future 到 retrieve  Future associated 使用  completion 的 c10d.进程组.Work. get_future 是 currently supported 用于 NCCL 和 also supported 用于 most 操作 在 GLOO 和 MPI, except 用于 peer 到 peer 操作 (send/recv).

Grad 桶’s tensors 将 not 是 predivided 由 world_size. User 是 responsible 到 divide 由  world_size 在 case 的 操作 like 全归约.

DDP 通信 钩子 可以 only 是 registered once 和 应该 是 registered before calling backward.

 Future object that 钩子 returns 应该 contain  single 张量 that 有  same shape 使用  tensors inside grad 桶.

get_future API supports NCCL, 和 partially GLOO 和 MPI backends (no support 用于 peer-到-peer 操作 like send/recv) 和 将 return  torch.futures.Future.

Below 是  示例 的  noop 钩子 that returns  same 张量.

Below 是  示例 的  并行 SGD 算法 where 梯度 是 encoded before 全归约, 和 then decoded after 全归约.

---

## DDP 通信 钩子#

**URL:** https://pytorch.org/docs/stable/ddp_comm_hooks.html

**Contents:**
- DDP 通信 钩子#
- How 到 Use  通信 钩子?#
- What 做  通信 钩子 Operate 在?#
- Default 通信 钩子#
- PowerSGD 通信 钩子#
  - PowerSGD 状态#
  - PowerSGD 钩子#
- Debugging 通信 钩子#
- Checkpointing 的 通信 钩子#
- Acknowledgements#

Created 在: Jun 06, 2025 | Last Updated 在: Jun 06, 2025

DDP 通信 钩子 是  generic 接口 到 control how 到 communicate 梯度 across workers 由 overriding  vanilla 全归约 在 分布式数据并行.  few built-在 通信 钩子 是 provided, 和 users 可以 easily apply any 的 these 钩子 到 optimize 通信. Besides,  钩子 接口 可以 also support user-defined 通信 strategies 用于 more advanced use cases.

到 use  通信 钩子,  user just needs 到 let  DDP 模型 register  钩子 before  训练 loop as below.

torch.nn.并行.分布式数据并行.register_comm_hook()

 通信 钩子 provides  flexible way 到 全归约 梯度. Therefore, it mainly operates 在  梯度 在 each 副本 before 全归约, which 是 bucketized 到 increase  重叠 between 通信 和 计算. Particularly, torch.分布式.GradBucket represents  桶 的 梯度 tensors 到 是 allreduced.

This class mainly passes  flattened 梯度 张量 (returned 由 buffer()) 到 DDP 通信 钩子. This 张量 可以 是 further decomposed into  list 的 per-参数 tensors within this 桶 (returned 由 get_per_parameter_tensors()) 到 apply 层-wise 操作.

Since  桶 是 rebuilt after  first 迭代, 应该 not rely 在  indices at  beginning 的 训练.

 index 的  桶 that stores 梯度 的  few contiguous 层. All  梯度 是 bucketized.

 flattened 1D torch.张量 buffer, which 可以 是 further decomposed into  list 的 per-参数 tensors within this 桶.

 list 的 torch.张量. Each 张量 在  list corresponds 到  梯度.

Whether this 桶 是  last 桶 到 全归约 在  迭代. This also means that this 桶 corresponds 到  first few 层 在  前向传播.

Replaces  张量 在  桶 使用  输入 张量 buffer.

 list 的 torch.张量. Each 张量 在  list corresponds 到  模型 参数.

Default 通信 钩子 是 simple stateless 钩子, so  输入 状态 在 register_comm_hook 是 either  进程 group 或 None.  输入 桶 是  torch.分布式.GradBucket object.

Call 全归约 using GradBucket tensors.

Once 梯度 tensors 是 aggregated across all workers, its then callback takes  mean 和 returns  result.

If user registers this DDP 通信 钩子, DDP results 是 expected 到 是 same as  case where no 钩子 是 registered. Hence, this won’t change behavior 的 DDP 和 user 可以 use this as  reference 或 modify this 钩子 到 log useful information 或 any other purposes while unaffecting DDP behavior.

Compress 由 casting GradBucket 到 torch.float16 divided 由 进程 group size.

This DDP 通信 钩子 implements  simple 梯度 compression approach that casts GradBucket 张量 到 half-precision floating-point format (torch.float16) 和 then divides it 由  进程 group size. It allreduces those float16 梯度 tensors. Once compressed 梯度 tensors 是 allreduced,  chained callback decompress casts it back 到  输入 数据 type (such as float32).

Warning: This API 是 experimental, 和 it requires NCCL version later than 2.9.6.

This DDP 通信 钩子 implements  simple 梯度 compression approach that casts GradBucket 张量 到 half-precision Brain floating point format (torch.bfloat16) 和 then divides it 由  进程 group size. It allreduces those bfloat16 梯度 tensors. Once compressed 梯度 tensors 是 allreduced,  chained callback decompress casts it back 到  输入 数据 type (such as float32).

Additionally,  通信 钩子 wrapper 是 provided 到 support fp16_compress_hook() 或 bf16_compress_hook() as  wrapper, which 可以 是 combined 使用 other 通信 钩子.

Cast 输入 张量 到 torch.float16, cast result 的 钩子 back 到 输入 dtype.

This wrapper casts  输入 梯度 张量 的  given DDP 通信 钩子 到 half-precision floating point format (torch.float16), 和 casts  resulting 张量 的  given 钩子 back 到  输入 数据 type, such as float32. Therefore, fp16_compress_hook 是 equivalent 到 fp16_compress_wrapper(allreduce_hook).

Callable[[Any, GradBucket], Future[张量]]

Warning: This API 是 experimental, 和 it requires NCCL version later than 2.9.6.

This wrapper casts  输入 梯度 张量 的  given DDP 通信 钩子 到 half-precision Brain floating point format (torch.bfloat16), 和 casts  resulting 张量 的  given 钩子 back 到  输入 数据 type, such as float32.

Therefore, bf16_compress_hook 是 equivalent 到 bf16_compress_wrapper(allreduce_hook).

Callable[[Any, GradBucket], Future[张量]]

PowerSGD (Vogels et al., NeurIPS 2019) 是  梯度 compression 算法, which 可以 provide very high compression rates 和 accelerate bandwidth-bound 分布式 训练. This 算法 needs 到 maintain both some hyperparameters 和  internal 状态. Therefore, PowerSGD 通信 钩子 是  stateful 钩子, 和  user needs 到 provide  状态 object defined as below.

Store both  算法’s hyperparameters 和 internal 状态 用于 all 梯度 during 训练.

Particularly, matrix_approximation_rank 和 start_powerSGD_iter 是  main hyperparameters that 应该 是 tuned 由  user. 用于 性能, we suggest 到 keep binary hyperparameters use_error_feedback 和 warm_start 在.

matrix_approximation_rank controls  size 的 compressed low-rank tensors, which determines  compression rate.  lower  rank,  stronger  compression.

1.1. If matrix_approximation_rank 是 too low,  full 模型 quality 将 need more 训练 steps 到 reach 或 将 never reach 和 yield 损失 在 accuracy.

1.2.  increase 的 matrix_approximation_rank 可以 substantially increase  计算 costs 的  compression, 和  accuracy 可能 not 是 further improved beyond  certain matrix_approximation_rank threshold.

到 tune matrix_approximation_rank, we suggest 到 start 从 1 和 increase 由 factors 的 2 (like  exponential grid search, 1, 2, 4, …), until  satisfactory accuracy 是 reached. Typically only  small value 1-4 是 used. 用于 some NLP tasks (as shown 在 Appendix D 的  original paper), this value 有 是 increased 到 32.

start_powerSGD_iter defers PowerSGD compression until 步骤 start_powerSGD_iter, 和 vanilla 全归约 runs prior 到 步骤 start_powerSGD_iter. This hybrid scheme 的 vanilla 全归约 + PowerSGD 可以 effectively improve  accuracy, even  relatively small matrix_approximation_rank 是 used. This 是 because that,  beginning 的 训练 phase 是 usually very sensitive 到 inaccurate 梯度, 和 compressing 梯度 too early 可能 make  训练 quickly take  suboptimal trajectory, which 可以 result 在  irrecoverable impact 在  accuracy.

到 tune start_powerSGD_iter, we suggest 到 start 使用 10% 的 total 训练 steps, 和 increase it until  satisfactory accuracy 是 reached. If there 是  warm-up stage 在  训练, start_powerSGD_iter typically 应该 是 no less than  number 的 warm-up steps.

min_compression_rate 是  minimum compression rate required when  层 是 compressed. Due 到  计算 overheads incurred 由  compression,  张量 是 worth compressing only if there 可以 是 sufficient saving 在 bandwidth, where (num_rows + num_cols) * matrix_approximation_rank * min_compression_rate < num_rows * num_cols. If  specified compression rate threshold cannot 是 satisfied,  张量 将 是 directly allreduced without compression.

Compression statistics 是 logged every compression_stats_logging_frequency iterations once PowerSGD compression starts.

orthogonalization_epsilon 可以 是  very small value (e.g., 1e-8) added 到 every normalized matrix column 在 orthogonalization 步骤, 到 prevent div-由-zero error if any column 有 all 0s. If this 可以 already 是 prevented (e.g., 由 批归一化),  epsilon 的 0 是 recommended 用于 accuracy.

batch_tensors_with_same_shape controls whether 到 compress 和 decompress tensors 使用 same shape 在  batched 操作 到 achieve higher parallelism. Note that you 应该 also increase  桶 size (i.e., bucket_cap_mb arg 在 DDP constructor) 到 make more same-shaped tensors appear 在  same 桶, however this 可能 归约  重叠 between 计算 和 通信, 和 increase  内存 footprint due 到 stacking  tensors 的  same shape. Set 到 True if  compression / decompression 计算 是  bottleneck.

If error feedback 或 warm-up 是 enabled,  minimum value 的 start_powerSGD_iter allowed 在 DDP 是 2. This 是 because there 是 another internal 优化 that rebuilds 桶 at 迭代 1 在 DDP, 和 this 可以 conflict 使用 any 张量 memorized before  rebuild 进程.

PowerSGD typically requires extra 内存 的  same size as  模型’s 梯度 到 enable error feedback, which 可以 compensate 用于 biased compressed 通信 和 improve accuracy.

PowerSGD 钩子 可能 conflict 使用 Apex automatic mixed precision package. Please use PyTorch native automatic mixed precision package instead.

Implement PowerSGD 算法.

This DDP 通信 钩子 implements PowerSGD 梯度 compression 算法 described 在  paper. Once 梯度 tensors 是 aggregated across all workers, this 钩子 applies compression as follows:

Views  输入 flattened 1D 梯度 张量 as  list 的 per-参数 tensors, 和 divides all  tensors into two groups:

1.1  tensors that 应该 是 compressed before 全归约, because  compression 可以 give enough saving 在 bandwidth.

1.2 Rest 的  tensors 将 是 directly allreduced without compression, including all  vector tensors (用于 biases).

Handles uncompressed tensors:

2.1. Allocate contiguous 内存 用于 those uncompressed tensors, 和 allreduces all  uncompressed tensors as  批次, without compression;

2.2. Copies  individual uncompressed tensors 从  contiguous 内存 back 到  输入 张量.

Handles  tensors that 应该 是 compressed 由 PowerSGD compression:

3.1. 用于 each 张量 M, creates two low-rank tensors P 和 Q 用于 decomposing M, such that M = PQ^T, where Q 是 initialized 从  standard normal distribution 和 orthogonalized;

3.2. Computes each P 在 Ps, which 是 equal 到 MQ;

3.3. Allreduces Ps as  批次;

3.4. Orthogonalizes each P 在 Ps;

3.5. Computes each Q 在 Qs, which 是 approximately equal 到 M^TP;

3.6. Allreduces Qs as  批次;

3.7. Computes each M among all  compressed tensors, which 是 approximately equal 到 PQ^T.

Note that this 通信 钩子 enforces vanilla 全归约 用于  first 状态.start_powerSGD_iter iterations. This not only gives  user more control over  tradeoff between speedup 和 accuracy, but also helps abstract away some complexity 的  internal 优化 的 DDP 用于 future 通信 钩子 developers.

状态 (PowerSGDState) – 状态 information 到 configure  compression rate 和 support error feedback, warm start, etc. 到 tune  compression configs, mainly need 到 tune matrix_approximation_rank, start_powerSGD_iter 和 min_compression_rate.

桶 (dist.GradBucket) – 桶 that stores  1D flattened 梯度 张量 that batches multiple per-variable tensors. Note that since DDP comm 钩子 only supports single 进程 single 设备 mode, only exactly one 张量 是 stored 在 this 桶.

Future handler 的  通信, which updates  梯度 在 place.

Implement simplified PowerSGD 算法.

This DDP 通信 钩子 implements  simplified PowerSGD 梯度 compression 算法 described 在  paper. This variant 做 not compress  梯度 层 由 层, but instead compresses  flattened 输入 张量 that batches all  梯度. Therefore, it 是 faster than powerSGD_hook(), but usually results 在  much lower accuracy, unless matrix_approximation_rank 是 1.

Increasing matrix_approximation_rank here 可能 not necessarily increase  accuracy, because batching per-参数 tensors without column/row alignment 可以 destroy low-rank structure. Therefore,  user 应该 always consider powerSGD_hook() first, 和 only consider this variant when  satisfactory accuracy 可以 是 achieved when matrix_approximation_rank 是 1.

Once 梯度 tensors 是 aggregated across all workers, this 钩子 applies compression as follows:

Views  输入 flattened 1D 梯度 张量 as  square-shaped 张量 M 使用 0 paddings;

Creates two low-rank tensors P 和 Q 用于 decomposing M, such that M = PQ^T, where Q 是 initialized 从  standard normal distribution 和 orthogonalized;

Computes P, which 是 equal 到 MQ;

Computes Q, which 是 approximately equal 到 M^TP;

Computes M, which 是 approximately equal 到 PQ^T.

Truncates  输入 张量 到  original length.

Note that this 通信 钩子 enforces vanilla 全归约 用于  first 状态.start_powerSGD_iter iterations. This not only gives  user more control over  tradeoff between speedup 和 accuracy, but also helps abstract away some complexity 的  internal 优化 的 DDP 用于 future 通信 钩子 developers.

状态 (PowerSGDState) – 状态 information 到 configure  compression rate 和 support error feedback, warm start, etc. 到 tune  compression configs, mainly need 到 tune matrix_approximation_rank 和 start_powerSGD_iter.

桶 (dist.GradBucket) – 桶 that stores  1D flattened 梯度 张量 that batches multiple per-variable tensors. Note that since DDP comm 钩子 only supports single 进程 single 设备 mode, only exactly one 张量 是 stored 在 this 桶.

Future handler 的  通信, which updates  梯度 在 place.

As  name implies, debugging 通信 钩子 是 only used 用于 debugging 和 性能 优化 purpose.

Debugging 通信 钩子 做 not necessarily 输出  correct results.

Return  future that wraps  输入, so it 是  no-op that 做 not incur any 通信 overheads.

This 钩子 应该 only 是 used 用于 headroom analysis 的 全归约 优化, instead 的  normal 梯度 同步. 用于 示例, if only less than 10% speedup 的 训练 time 可以 是 observed after this 钩子 是 registered, it usually implies that 全归约 是 not  性能 bottleneck 用于 this case. Such instrumentation 可以 是 particularly useful if GPU traces cannot 是 easily retrieved 或  trace analysis 是 complicated some factors such as  重叠 between 全归约 和 计算 或  desynchronization across ranks.

 stateful 通信 钩子 可以 是 saved as  part 的 模型 checkpointing 到 enable trainer restarts. 到 make  钩子 serializable, __setstate__ 和 __getstate__ 应该 是 defined.

__getstate__ 应该 exclude non-serializable attributes 从  returned dictionary.

__setstate__ 应该 properly initialize non-serializable attributes, excluded 从  provided 状态.

PowerSGDState 有 __setstate__ 和 __getstate__ implemented 和 可以 是 used as  reference.

Return  Dict[str, Any] which 将 是 pickled 和 saved.

process_group 是 not serializable 和 excluded 从  returned 状态.

Take  provided 状态 和 set 到 this PowerSGDState instance.

process_group 是 set 到 default.

Here 是  simple, end-到-end 示例 的 saving 和 reloading PowerSGD 状态 和 钩子.

Many thanks 到 PowerSGD paper author Thijs Vogels 用于  code review 在 PowerSGD 通信 钩子, as well as  comparison experiments, which show that  性能 的 PowerSGD 通信 钩子 是 在 par 使用  实现 在  original paper.

---

## 分布式 Checkpoint - torch.分布式.checkpoint#

**URL:** https://pytorch.org/docs/stable/分布式.checkpoint.html

**Contents:**
- 分布式 Checkpoint - torch.分布式.checkpoint#
- Additional resources:#

Created 在: Nov 16, 2022 | Last Updated 在: Sep 04, 2025

分布式 Checkpoint (DCP) support loading 和 saving 模型 从 multiple ranks 在 并行. It handles load-time resharding which enables saving 在 one cluster topology 和 loading into another.

DCP 是 different than torch.save 和 torch.load 在  few significant ways:

It produces multiple files per checkpoint, 使用 at least one per rank.

It operates 在 place, meaning that  模型 应该 allocate its 数据 first 和 DCP uses that storage instead.

 entrypoints 到 load 和 save  checkpoint 是  following:

Getting Started 使用 分布式 Checkpoint (DCP)

Asynchronous Saving 使用 分布式 Checkpoint (DCP)

TorchTitan Checkpointing Docs

TorchTitan DCP 实现

Enum 用于 async checkpointer type.

This class contains futures 用于 staging 和 upload completion. It 是 returned 由 async_save(). staging_completion 是  future that indicates when local copy 的 state_dict 是 complete. upload_completion 是  future that indicates when  checkpoint completed saving.

Save  分布式 模型 在 SPMD style.

This 函数 是 different 从 torch.save() as it handles ShardedTensor , 和 DTensor 由 having each rank only save their local shards.

用于 each Stateful object (having both  state_dict 和  load_state_dict), save 将 call state_dict before serialization.

There 是 no guarantees 的 Backwards Compatibility across PyTorch versions 用于 saved state_dicts.

If using  process_group argument, make sure that only its ranks call save_state_dict 和 that all 数据 在 state_dict belong 到 it.

When saving checkpoint 用于 FSDP’s ShardingStrategy.HYBRID_SHARD, only one 的  shard_group 应该 是 calling save_state_dict 和  corresponding 进程 group needs 到 是 passed 在.

state_dict 在  local 进程.

state_dict (Dict[str, Any]) –  state_dict 到 save.

checkpoint_id (Union[str, os.PathLike, None]) –  ID 的 this checkpoint instance.  meaning 的  checkpoint_id depends 在  storage. It 可以 是  path 到  folder 或 到  file. It 可以 also 是  key if  storage 是  key-value store. (Default: None)

storage_writer (Optional[StorageWriter]) – Instance 的 StorageWriter used 到 perform writes. If this 是 not specified, DCP 将 automatically infer  writer based 在  checkpoint_id. If checkpoint_id 是 also None,  exception 将 是 raised. (Default: None)

planner (Optional[SavePlanner]) – Instance 的 SavePlanner. If this 是 not specified,  default planner 将 是 used. (Default: None)

process_group (Optional[进程组]) – 进程组 到 是 used 用于 cross-rank 同步. (Default: None)

no_dist (bool) – If True, this 函数 将 assume  intent 是 到 load  checkpoint 在  single rank/进程. (Default: False)

use_collectives (bool) – If False, this 函数 将 assume  intent 是 到 save  checkpoint without using cross-rank 同步. (Default: True) This 配置 是 experimental 和 应该 是 used 使用 caution. It 将 change  format 的  saved checkpoint 和 可能 not 是 backward compatible.

Metadata object 用于  saved checkpoint.

save_state_dict uses collectives 到 coordinate writes across ranks. 用于 NCCL-based 进程 groups, internal 张量 representations 的 objects 必须 是 moved 到  GPU 设备 before 通信 takes place. 在 this case,  设备 used 是 given 由 torch.cuda.current_device() 和 it 是  user’s responsibility 到 ensure that this 是 set so that each rank 有  individual GPU, via torch.cuda.set_device().

Asynchronous version 的 save. This code first de-stages  state_dict 在 到  staging storage (defaults 到 CPU 内存), 和 then calls  save 在  separate thread.

This feature 是 experimental 和 subject 到 change. 必须 CALL CLOSE AFTER LAST CHECKPOINT 是 SAVED

state_dict (Dict[str, Any]) –  state_dict 到 save.

checkpoint_id (Union[str, os.PathLike, None]) –  ID 的 this checkpoint instance.  meaning 的  checkpoint_id depends 在  storage. It 可以 是  path 到  folder 或 到  file. It 可以 also 是  key if  storage 是  key-value store. (Default: None)

storage_writer (Optional[StorageWriter]) – Instance 的 StorageWriter used 到 perform ‘stage’ 和 ‘save’. If this 是 not specified, DCP 将 automatically infer  writer based 在  checkpoint_id. If checkpoint_id 是 also None,  exception 将 是 raised. (Default: None)

planner (Optional[SavePlanner]) – Instance 的 SavePlanner. If this 是 not specified,  default planner 将 是 used. (Default: None)

process_group (Optional[进程组]) – 进程组 到 是 used 用于 cross-rank 同步. (Default: None)

async_checkpointer_type (AsyncCheckpointerType) – whether 到 做 checkpoint 在 separate thread 或 进程 (Default: AsyncCheckpointerType.THREAD)

async_stager (AsyncStager) – provides staging 实现. If storage_writer implements AsyncStager 和 async_stager 是 provided, async_stager 将 是 used 用于 staging

no_dist (bool) – If True, this 函数 将 assume  intent 是 到 save  checkpoint 在  single rank/进程. (Default: False)

use_collectives (bool) – If False, Save  checkpoint without rank coordination. (Default: True) This 配置 是 experimental 和 应该 是 used 使用 caution. It 将 change  format 的  saved checkpoint 和 可能 not 是 backward compatible.

 future holding  resultant Metadata object 从 save.

This method 是 deprecated. Please switch 到 ‘save’.

Load  checkpoint into  分布式 状态 dict 在 SPMD style.

Each rank 必须 有  same keys 在 their state_dict provided 到 this API. Mismatched keys 可能 result 在 hangs 或 errors. If unsure, you 可以 use  utils._assert_same_keys API 到 check (but 可能 incur 通信 costs).

Each rank 将 try 到 read  least amount 的 数据 necessary 到 fulfill  requested state_dict. When loading ShardedTensor 或 DTensor instances, each rank only reads 数据 用于 their local shards.

用于 each Stateful object (having both  state_dict 和  load_state_dict), load 将 first call state_dict before attempting deserialization, followed 由 load_state_dict once  deserialization 是 complete. 用于 each non-Stateful object, load 将 deserialize  object, 和 then replace it 在  state_dict 使用  deserialized object.

All tensors 在 state_dict 必须 是 allocated 在 their destination 设备 prior 到 calling this 函数.

All non-张量 数据 是 loaded using torch.load() 和 modified 在 place 在 state_dict.

Users 必须 call load_state_dict 在  root 模块 到 ensure load pos-processing 和 non-张量 数据 properly propagates.

state_dict (Dict[str, Any]) –  state_dict 到 load  checkpoint into.

checkpoint_id (Union[str, os.PathLike, None]) –  ID 的 this checkpoint instance.  meaning 的  checkpoint_id depends 在  storage. It 可以 是  path 到  folder 或 到  file. It 可以 also 是  key if  storage 是  key-value store. (Default: None)

storage_reader (Optional[StorageReader]) – Instance 的 StorageWriter used 到 perform reads. If this 是 not specified, DCP 将 automatically infer  reader based 在  checkpoint_id. If checkpoint_id 是 also None,  exception 将 是 raised. (Default: None)

planner (Optional[LoadPlanner]) – Instance 的 LoadPlanner. If this 是 not specified,  default planner 将 是 used. (Default: None)

process_group (Optional[进程组]) – 进程组 到 是 used 用于 cross-rank 同步. (Default: None)

no_dist (bool) – If True, this 函数 将 assume  intent 是 到 load  checkpoint without using cross-rank 同步. (Default: False)

load_state_dict uses collectives 到 coordinate reads across ranks. 用于 NCCL-based 进程 groups, internal 张量 representations 的 objects 必须 是 moved 到  GPU 设备 before 通信 takes place. 在 this case,  设备 used 是 given 由 torch.cuda.current_device() 和 it 是  user’s responsibility 到 ensure that this 是 set so that each rank 有  individual GPU, via torch.cuda.set_device().

This method 是 deprecated. Please switch 到 ‘load’.

 following 模块 是 also useful 用于 additional customization 的  staging mechanisms used 用于 asynchronous checkpointing (torch.分布式.checkpoint.async_save):

This protocol 是 meant 到 provide customization 和 extensibility 用于 dcp.async_save, allowing users 到 customize how 数据 是 staged previous 到 executing  usual dcp.save path 在 并行.  expected order 的 操作 (concretely defined 在 torch.分布式.state_dict_saver.async_save) 是  following:

This call gives  AsyncStager  opportunity 到 ‘stage’  state_dict.  expectation 和 purpose 的 staging 在 this context 是 到 create  “训练-safe” representation 的  状态 dict, meaning that any updates 到 模块 数据 after staging 是 complete 应该 not 是 reflected 在  状态 dict returned 从 this method. 用于 示例, 在  default case  copy 的  entire 状态 dict 是 created 在 CPU RAM 和 returned here, allowing users 到 continue 训练 without risking changes 到 数据 which 是 正在 serialized.

用于 serializing  state_dict 和 writing it 到 storage.

 serialization thread starts 和 before returning 从 dcp.async_save. If this 是 set 到 False,  assumption 是  user 有 defined  custom 同步 point 用于  purpose 的 further optimizing save latency 在  训练 loop (用于 示例, 由 overlapping staging 使用  forward/反向传播), 和 it 是  respondsibility 的  user 到 call AsyncStager.synchronize_staging at  appropriate time.

Clean up all resources used 由  stager.

Whether 到 synchronize after executing  stage.

Returns  “staged” copy 的 state_dict.  expectation 的  staged copy 是 that it 是 inoculated 从 any updates incurred after  stage call 是 complete.

Union[Future[dict[str, Union[~StatefulT, Any]]], dict[str, Union[~StatefulT, Any]]]

在  case stage 是 async 在 some way, this method 应该 是 called 到 ensure staging 是 complete 和 it 是 safe 到 begin modifying  original state_dict

DefaultStager provides  full-featured staging 实现 that combines multiple 优化 techniques 用于 efficient checkpoint preparation.

 staging 进程 works as follows: 1. 状态 dictionary 是 submitted 用于 staging (sync 或 async) 2. Tensors 是 copied 从 GPU 到 optimized CPU storage 3. CUDA 操作 是 synchronized if non-blocking copies 是 used 4. Staged 状态 dictionary 是 returned 或 made available via Future

# Synchronous staging stager = DefaultStager(StagingOptions(use_async_staging=False)) staged_dict = stager.stage(state_dict) stager.close()

# Asynchronous staging stager = DefaultStager(StagingOptions(use_async_staging=True)) future = stager.stage(state_dict) # … 做 other work … staged_dict = future.result() stager.close()

# Context manager pattern (recommended) stager = DefaultStager(config) 使用 stager: result = stager.stage(state_dict)

Async staging provides best 性能 when 模型 计算 可以 重叠 使用 staging 操作

Pinned 内存 improves CPU-GPU transfer speeds but uses more 内存

Shared 内存 allows efficient IPC 到 checkpoint 进程

Non-blocking copies 归约 GPU idle time during 内存 transfers

DefaultStager 是 not thread-safe. Each thread 应该 use its own instance, 或 external 同步 应该 是 provided.

Clean up all resources used 由  DefaultStager. Shuts down  ThreadPoolExecutor used 用于 async staging 操作 和 cleans up  underlying StateDictStager’s cached storages. 应该 是 called when  stager 是 no longer needed 到 prevent resource leaks, especially 在 long-running applications. After calling close(),  stager 应该 not 是 used 用于 further staging 操作.

stager = DefaultStager(StagingOptions(use_async_staging=True)) future = stager.stage(state_dict) result = future.result() stager.close() # Clean up all resources

This 函数 是 responsible 用于 staging staging  state_dict. See class docstring 用于 more details 在 staging. If use_async_staging 是 True, it 将 return  Future object that 将 是 fulfilled when staging 是 complete. If use_async_staging 是 False, it 将 return  fully staged state_dict.

state_dict (STATE_DICT_TYPE) –  state_dict 到 是 staged.

Union[dict[str, Union[~StatefulT, Any]], Future[dict[str, Union[~StatefulT, Any]]]]

When use_async_staging 是 True, this method 将 wait until staging 是 complete. If use_async_staging 是 False, this method 是  no-op.

配置 options 用于 checkpoint staging behavior.

use_pinned_memory (bool) – Enable pinned 内存 allocation 用于 faster CPU-GPU transfers. Requires CUDA 到 是 available. Default: True

use_shared_memory (bool) – Enable shared 内存 用于 multi-进程 scenarios. Useful when multiple 进程 need access 到  same staged 数据. Default: True

use_async_staging (bool) – Enable asynchronous staging using  background thread pool. Allows overlapping 计算 使用 staging 操作. Requires CUDA. Default: True

use_non_blocking_copy (bool) – Use non-blocking 设备 内存 copies 使用 stream 同步. Improves 性能 由 allowing CPU work 到 continue during GPU transfers. Default: True

CUDA-dependent features 将 raise exception if CUDA 是 not available.

 实现 的 AsyncStager which stages  state_dict 在 CPU RAM 和 blocks until  copy 是 complete. This 实现 also provides  option 到 optimize stage latency using pinned 内存.

N.B. synchronize_staging 是  no-op 在 this case.

Returns  copy 的 state_dict 在  CPU.

dict[str, Union[~StatefulT, Any]]

No-op 函数, since staging 是 blocking.

在 addition 到  above entrypoints, Stateful objects, as described below, provide additional customization during saving/loading

Stateful protocol 用于 objects that 可以 是 checkpointed 和 restored.

Restore  object’s 状态 从  provided state_dict.

state_dict (dict[str, Any]) –  状态 dict 到 restore 从

Objects 应该 return their state_dict representation as  dictionary.  输出 的 this 函数 将 是 checkpointed, 和 later restored 在 load_state_dict().

Because 的  inplace nature 的 restoring  checkpoint, this 函数 是 also called during torch.分布式.checkpoint.load.

 objects 状态 dict

This 示例 shows how 到 use Pytorch 分布式 Checkpoint 到 save  FSDP 模型.

 following types define  IO 接口 used during checkpoint:

接口 used 由 load_state_dict 到 read 从 storage.

One StorageReader instance acts as both  coordinator 和  follower 在  分布式 checkpoint. As part 的 初始化, each instance 是 told its role.

 subclass 应该 expected  following 序列 的 calls 由 load_state_dict:

(all ranks) set checkpoint_id if users 传播  valid checkpoint_id.

(all ranks) read_metadata()

(all ranks) set_up_storage_reader()

(all ranks) prepare_local_plan()

(coordinator) prepare_global_plan()

(all ranks) read_data()

Perform centralized planning 的 storage loading.

This method 是 only called 在  coordinator instance.

While this method 可以 produce  completely different plan,  preferred way 是 到 store storage specific 数据 在 LoadPlan::storage_data.

plans (list[torch.分布式.checkpoint.planner.LoadPlan]) –  list 的 LoadPlan instances, one 用于 each rank.

 list 的 transformed LoadPlan after storage global planning

list[torch.分布式.checkpoint.planner.LoadPlan]

Perform storage-specific local planning.

While this method 可以 produce  completely different plan,  recommended way 是 到 store storage specific 数据 在 LoadPlan::storage_data.

plan (LoadPlan) –  local plan 从  LoadPlan 在 use.

 transformed LoadPlan after storage local planning

Read all items 从 plan using planner 到 resolve  数据.

 subclass 应该 call LoadPlanner::load_bytes 到 deserialize  BytesIO object into  right place.

 subclass 应该 call LoadPlanner::resolve_tensor 到 get access 到  tensors that 在 应该 load 数据 into.

It’s  StorageLayer responsibility 到 properly schedule any cross 设备 copies required.

plan (LoadPlan) –  local plan 到 execute 在

planner (LoadPlanner) –  planner object 到 use 到 resolve items.

 future that completes once all reads 是 finished.

Read  checkpoint metadata.

 metadata object associated 使用  checkpoint 正在 loaded.

Calls 到 indicates  brand new checkpoint read 是 going 到 happen.  checkpoint_id 可能 是 present if users set  checkpoint_id 用于 this checkpoint read.  meaning 的  checkpoint_id 是 storage-dependent. It 可以 是  path 到  folder/file 或  key 用于  key-value storage.

checkpoint_id (Union[str, os.PathLike, None]) –  ID 的 this checkpoint instance.  meaning 的  checkpoint_id depends 在  storage. It 可以 是  path 到  folder 或 到  file. It 可以 also 是  key if  storage 是 more like  key-value store. (Default: None)

Initialize this instance.

metadata (Metadata) –  metadata schema 到 use.

is_coordinator (bool) – Whether this instance 是 responsible 用于 coordinating  checkpoint.

Check if  given checkpoint_id 是 supported 由  storage. This allow us 到 enable automatic storage selection.

接口 used 由 save_state_dict 到 write 到 storage.

One StorageWriter instance acts as both  coordinator 和  follower 在  分布式 checkpoint. As part 的 初始化, each instance 是 told its role.

 subclass 应该 expect  following 序列 的 calls.

(all ranks) set checkpoint_id if users 传播  valid checkpoint_id.

(all ranks) set_up_storage_writer()

(all ranks) prepare_local_plan()

(coordinator) prepare_global_plan()

(all ranks) write_data()

(coordinator) finish()

Write  metadata 和 marks  current checkpoint as successful.

 actual format/schema used 用于 serializing metadata 是  实现 detail.  only requirement 是 that it’s recoverable 在 到  same object 图.

metadata (Metadata) – metadata 用于  new checkpoint

results (list[list[torch.分布式.checkpoint.storage.WriteResult]]) –  list 的 WriteResults 从 all ranks.

Perform centralized planning 的 storage.

This method 是 only called 在  coordinator instance.

While this method 可以 produce  completely different plan,  preferred way 是 到 store storage specific 数据 在 SavePlan::storage_data.

plans (list[torch.分布式.checkpoint.planner.SavePlan]) –  list 的 SavePlan instances, one 用于 each rank.

 list 的 transformed SavePlan after storage global planning

list[torch.分布式.checkpoint.planner.SavePlan]

Perform storage-specific local planning.

While this method 可以 produce  completely different plan,  recommended way 是 到 store storage specific 数据 在 SavePlan::storage_data.

plan (SavePlan) –  local plan 从  SavePlanner 在 use.

 transformed SavePlan after storage local planning

Calls 到 indicates  brand new checkpoint write 是 going 到 happen.  checkpoint_id 可能 是 present if users set  checkpoint_id 用于 this checkpoint write.  meaning 的  checkpoint_id 是 storage-dependent. It 可以 是  path 到  folder/file 或  key 用于  key-value storage.

checkpoint_id (Union[str, os.PathLike, None]) –  ID 的 this checkpoint instance.  meaning 的  checkpoint_id depends 在  storage. It 可以 是  path 到  folder 或 到  file. It 可以 also 是  key if  storage 是  key-value store. (Default: None)

Initialize this instance.

is_coordinator (bool) – Whether this instance 是 responsible 用于 coordinating  checkpoint.

Return  storage-specific metadata. This 是 used 到 store additional information 在  checkpoint that 可以 是 useful 用于 providing request-level observability. StorageMeta 是 passed 到  SavePlanner during save calls. Returns None 由 default.

示例:

```python
from torch.distributed.checkpoint.storage import StorageMeta

class CustomStorageBackend:
    def get_storage_metadata(self):
        # Return storage-specific metadata that 将 是 stored 使用  checkpoint
        return StorageMeta()
```

This 示例 shows how  storage backend 可以 return `StorageMeta`
到 attach additional metadata 到  checkpoint.

Optional[StorageMeta]

Check if  given checkpoint_id 是 supported 由  storage. This allow us 到 enable automatic storage selection.

Write all items 从 plan using planner 到 resolve  数据.

 subclass 应该 call SavePlanner::resolve_data 在 each item 从  plan 到 get access 到  underlying object 到 write.

Subclasses 应该 lazily call resolve_data as it 可以 allocate 内存. 在 case 的 tensors, make following assumptions:

They 可能 是 在 any 设备, including not matching  one 在 WriteItem::tensor_data

They 可能 是 views 或 not contiguous. Only  projection needs 到 是 saved.

plan (SavePlan) –  save plan 到 execute.

planner (SavePlanner) – Planner object 到 是 used 到 resolve items 到 数据.

 future that completes 到  list 的 WriteResult

Future[list[torch.分布式.checkpoint.storage.WriteResult]]

 following types define  planner 接口 used during checkpoint:

Abstract class defining  protocol used 由 load_state_dict 到 plan  load 进程.

LoadPlanner 是 stateful objects that 可以 是 used 到 customize  whole load 进程.

LoadPlanner acts as  access proxy 到  state_dict, so any transformation done 到 it 将 是 visible 到  whole 进程.

 planner subclass 可以 expect  following 序列 的 calls during load_state_dict:

Signals  start 的 loading  checkpoint.

进程  state_dict 和 produces  LoadPlan that 将 是 sent 用于 global planning.

Takes  LoadPlan 从 all ranks 和 make any global decision.

This 是 called once per non-张量 value 在 state_dict.

They 是 called 在 pair 用于 each 张量 value 在 state_dict.

Users 是 recommended 到 extend DefaultLoadPlanner instead 的 this 接口 directly as most changes 可以 是 expressed 由 changes 在  single method.

There 是 two usual patterns 的 extension:

Rewriting state_dict. This 是  simplest way 到 extend  load 进程 as it doesn’t requite understanding  intrincacies 的 how LoadPlan works. We need 到 keep  reference 到  original state_dict as load happens 在 place so we need 到 是 able 到 perform it 在 place

Modifying resolve_tensor 和 commit_tensor 到 handle load time transformation.

Call once  StorageReader finished loading 数据 into 张量.

 provided 张量 是  same one returned 由  call 到 resolve_tensor. This method 是 only needed if this LoadPlanner needs 到 post 进程 张量 prior 到 copying it back 到  one 在  state_dict.

 contents 的 张量 将 follow its 设备 同步 模型.

Compute  global load plan 和 return plans 用于 each rank.

. N.B. This 是 called 在  coordinator rank only

list[torch.分布式.checkpoint.planner.LoadPlan]

Create  LoadPlan based 在 state_dict 和 metadata provided 由 set_up_planner.

. N.B. This 是 called 在 every rank.

Accept  plan 从 coordinator 和 return final LoadPlan.

Load  item described 由 read_item``和 ``value.

This method 是 expected 到 modify 在-place  underlying state_dict.

 contents 的 value 是 defined 由  SavePlanner used 到 produce  checkpoint 正在 loaded.

Return  BytesIO 到 是 used 由  StorageReader 到 load read_item.

 BytesIO 应该 alias 使用 one 在  underlying state_dict as StorageReader 将 replace its contents.

Return  张量 described 由 read_item 到 是 used 由  StorageReader 到 load read_item.

 张量 应该 alias 使用 one 在  underlying state_dict as StorageReader 将 replace its contents. If, 用于 any reason, that’s not possible,  planner 可以 use  commit_tensor method 到 copy  数据 back 到  one 在 state_dict.

Initialize this instance 到 load 数据 into state_dict.

. N.B. This 是 called 在 every rank.

Abstract class defining  protocol used 由 save_state_dict 到 plan  save 进程.

SavePlanners 是 stateful objects that 可以 是 used 到 customize  whole save 进程.

SavePlanner acts as  access proxy 到  state_dict, so any transformation done 到 it 将 是 visible 到  whole 进程.

 planner subclass 可以 expect  following 序列 的 calls during save_state_dict:

Signals  start 的  checkpoint save.

进程  state_dict 和 produces  SavePlan that 将 是 sent 用于 global planning.

Takes  SavePlan 从 all ranks 和 make any global decision.

This gives each rank  chance 到 adjust 到 global planning decisions.

Lookups  value 在  state_dict 用于  storage 层 到 write.

Users 是 recommended 到 extend DefaultSavePlanner instead 的 this 接口 directly as most changes 可以 是 expressed 由 changes 在  single method.

There 是 3 usual patterns 的 extension:

Rewriting state_dict. This 是  simplest way 到 extend  save 进程 as it doesn’t requite understanding  intrincacies 的 how SavePlan works:

Modifying local plan 和 lookup 在 tandem. This 是 useful when fine control 的 how 数据 是 persisted

Using  global planning 步骤 到 make central decisions that 可以’t 是 made individually 由 each rank

Finally, some planners need 到 save additional metadata 在  checkpoint, this 是 accomplished 由 having each rank contribute their 数据 items 在  local plan 和  global planner aggregate them:

Compute  global checkpoint plan 和 return  local plan 的 each rank.

This 是 called 在  coordinator rank only.

tuple[list[torch.分布式.checkpoint.planner.SavePlan], torch.分布式.checkpoint.metadata.Metadata]

Compute  save plan 用于  current rank.

This 将 是 aggregated 和 passed 到 create_global_plan. Planner specific 数据 可以 是 passed through SavePlan::planner_data.

This 是 called 在 all ranks.

Merge  plan created 由 create_local_plan 和  result 的 create_global_plan.

This 是 called 在 all ranks.

Transform 和 prepare write_item 从 state_dict 用于 storage, ensuring idempotency 和 thread-safety.

Lookup  object associated 使用 write_item 在 state_dict 和 apply any transformation (such as serialization) prior 到  storage 层 consuming it.

Called 在 each rank multiple times, at least once per WriteItem 在  final SavePlan.

This method 应该 是 idempotent 和 thread-save. StorageWriter implementations 是 free 到 call it as frequently as they need.

Any transformation that allocates 内存 应该 是 lazily done when his method 是 called 在 order 到 归约 peak 内存 required 由 checkpointing.

When returning tensors, they 可以 是 在 any 设备 或 format, they 可以 是 views too. It’s  storage 层 responsibility 到 figure out how 到 save them.

Union[张量, BytesIO]

Initialize this planner 到 save state_dict.

Implementations 应该 save those values as they won’t 是 provided lated 在  save 进程.

This 是 called 在 all ranks.

Dataclass which holds information about what needs 到 是 written 到 storage.

Calculates  storage size 的  underlying 张量, 或 None if this 是 not  张量 write.

Optional[int] storage size, 在 bytes 的 underlying 张量 if any.

We provide  filesystem based storage 层:

return  checkpoint_id that 将 是 used 到 load  checkpoint.

Basic 实现 的 StorageWriter using file IO.

This 实现 makes  following assumptions 和 simplifications:

 checkpoint path 是  empty 或 non-existing directory.

File creation 是 atomic

 checkpoint consist 的 one file per write request plus  global .metadata file 使用  serialized metadata if rank coordination 是 enabled.  rank local __{rank}.metadata file 使用  serialized metadata if rank coordination 是 NOT enabled.

Override 的 AsyncStager.stage

dict[str, Union[~StatefulT, Any]]

We also provide other storage 层, including ones 到 interact 使用 HuggingFace safetensors:

.. autoclass:: torch.分布式.checkpoint.HuggingFaceStorageReader :members:

.. autoclass:: torch.分布式.checkpoint.HuggingFaceStorageWriter :members:

.. autoclass:: torch.分布式.checkpoint.QuantizedHuggingFaceStorageReader :members:

We provide default implementations 的 LoadPlanner 和 SavePlanner that 可以 handle all 的 torch.分布式 constructs such as FSDP, DDP, ShardedTensor 和 DistributedTensor.

Extension 从  planner 接口 到 make it easy 到 extend  default planner.

Extension 从  planner 接口 到 make it easy 到 extend  default planner.

DefaultLoadPlanner that adds multiple features 在 top 的 LoadPlanner.

在 particular it adds  following:

flatten_state_dict: Handle state_dict 使用 nested dicts flatten_sharded_tensors: 用于 FSDP 在 2D 并行 mode allow_partial_load: If False, 将 raise  runtime error if  key 是 present 在 state_dict, but not 在  checkpoint.

Extension 从  planner 接口 到 make it easy 到 extend  default planner.

Extension 从  planner 接口 到 make it easy 到 extend  default planner.

Due 到 legacy design decisions,  状态 dictionaries 的 FSDP 和 DDP 可能 有 different keys 或 fully qualified names (e.g., layer1.权重) even when  original unparallelized 模型 是 identical. Moreover, FSDP offers various types 的 模型 状态 dictionaries, such as full 和 sharded 状态 dictionaries. Additionally, 优化器 状态 dictionaries employ 参数 IDs instead 的 fully qualified names 到 identify 参数, potentially causing issues when parallelisms 是 used (e.g., pipeline parallelism).

到 tackle these challenges, we offer  collection 的 APIs 用于 users 到 easily manage state_dicts. get_model_state_dict() returns  模型 状态 dictionary 使用 keys consistent 使用 those returned 由  unparallelized 模型 状态 dictionary. Similarly, get_optimizer_state_dict() provides  优化器 状态 dictionary 使用 keys uniform across all parallelisms applied. 到 achieve this consistency, get_optimizer_state_dict() converts 参数 IDs 到 fully qualified names identical 到 those found 在  unparallelized 模型 状态 dictionary.

Note that results returned 由 these APIs 可以 是 used directly 使用  torch.分布式.checkpoint.save() 和 torch.分布式.checkpoint.load() methods without requiring any additional conversions.

set_model_state_dict() 和 set_optimizer_state_dict() 是 provided 到 load  模型 和 优化器 state_dict generated 由 由 their respective getter APIs.

Note that set_optimizer_state_dict() 可以 only 是 called before backward() 或 after 步骤() 是 called 在 optimizers.

Note that this feature 是 experimental, 和 API signatures 可能 change 在  future.

Return  模型 state_dict 和 optimizers state_dict.

get_state_dict 可以 进程 any 模块 that 是 parallelized 由 PyTorch FSDP/fully_shard, DDP/replicate, tensor_parallel/parallelize_module, 和 any combination 的 these parallelisms.  main functions 的 get_state_dict 是: 1.) returning  模型 和 优化器 state_dict that 可以 是 resharded 使用  different number 的 trainers 和/或 different parallelisms. 2.) hiding  parallelism-specific state_dict APIs. Users don’t 有 到 call these APIs. 3.) sanity checking  result state_dict.

 keys 的  result 状态 dictionary 是  canonical FQNs (Fully Qualified Names).  canonical FQN refers 到  FQN based 在  参数’s position 在  nn.模块 hierarchy. More specifically,  canonical FQN 到  参数 是  FQN returned 由 模块.named_parameters() 或 模块.named_buffers() when  模块 是 not 分布式 由 any parallelisms. Since  优化器 internally uses 参数 IDs 到 represent  参数, there 将 是  conversion 从  参数 IDs 到  canonical FQNs when calling this API.

get_state_dict 可以 also 进程  模块 that 是 not parallelized. 在 such  case, get_state_dict only performs one 函数 – converting  优化器 参数 IDs 到  canonical FQNs.

模型 (nn.模块) –  nn.模块 到  模型.

optimizers (Union[None, 优化器, Iterable[优化器]]) –  optimizers that 是 used 到 optimize 模型.

submodules (deprecated) – Optional[set[nn.模块]]: only return  模型 参数 that belong 到  submodules.

options (StateDictOptions) –  options 到 control how 模型 state_dict 和 优化器 state_dict 应该 是 returned. See StateDictOptions 用于  details.

Tuple that contain 模型 state_dict 和 优化器 state_dict.

Tuple[Dict[str, ValueType], OptimizerStateType]

Return  模型 state_dict 的 模型.

See get_state_dict 用于  detail usage.

模型 (nn.模块) –  nn.模块 到  模型.

submodules (deprecated) – Optional[set[nn.模块]]: only return  模型 参数 that belong 到  submodules.

options (StateDictOptions) –  options 到 control how 模型 state_dict 和 优化器 state_dict 应该 是 returned. See StateDictOptions 用于  details.

 state_dict 用于 模型.

Return  combined state_dict 用于 optimizers.

See get_state_dict 用于  detail usage.

模型 (nn.模块) –  nn.模块 到  模型.

optimizers (Union[None, 优化器, Iterable[优化器]]) –  optimizers that 是 used 到 optimize 模型.

submodules (deprecated) – Optional[set[nn.模块]]: only return  模型 参数 that belong 到  submodules.

options (StateDictOptions) –  options 到 control how 模型 state_dict 和 优化器 state_dict 应该 是 returned. See StateDictOptions 用于  details.

 state_dict 用于 optimizers.

Load  模型 state_dict 和 optimizers state_dict.

 counterpart 的 get_state_dict 到 set  state_dict 到  模型 和 optimizers.  given model_state_dict 和 optim_state_dict 做 not 有 到 是 returned 由 get_state_dict but 必须 meet  following requirements: 1) all FQNs 是 canonical FQNs as defined 在 get_state_dict, 2) if  张量 是 sharded, it 必须 是 either  ShardedTensor 或 DTensor, 3) 优化器 state_dict cannot contain  参数 IDs;  keys 应该 是  canonical FQNs.

是 called 在  optimizers. Otherwise,  优化器 states won’t 是 initialized correctly.

模型 (nn.模块) –  nn.模块 到  模型.

optimizers (Union[优化器, Iterable[优化器]]) –  optimizers that 是 used 到 optimize 模型.

model_state_dict (Dict[str, ValueType]) – (Union[Dict[nn.模块, Dict[str, ValueType]], Dict[str, ValueType]]):  模型 state_dict 到 load. If  key 的  model_state_dict 是 nn.模块,  key 是  submodule 的 模型 和  value 应该 是  state_dict 的  submodule. When loading  state_dict,  prefix 的  submodule 将 是 append 到  state_dict.

optim_state_dict (OptimizerStateType) – OptimizerStateType:  优化器 state_dict 到 load.

options (StateDictOptions) –  options 到 control how 模型 state_dict 和 优化器 state_dict 应该 是 loaded. See StateDictOptions 用于  details.

missing_keys 是  list 的 str containing  missing keys 的  模型 state_dict. unexpected_keys 是  list 的 str containing  unexpected keys 的  模型 state_dict.

missing_keys 是  list 的 str containing  missing keys 的  模型 state_dict.

unexpected_keys 是  list 的 str containing  unexpected keys 的  模型 state_dict.

NamedTuple 使用 missing_keys 和 unexpected_keys fields

Load  模型 state_dict.

 counterpart 的 get_model_state_dict 到 set  state_dict 到  模型. See set_state_dict 用于  detail usage.

模型 (nn.模块) –  nn.模块 到  模型.

model_state_dict (Dict[str, ValueType]) – (Dict[str, ValueType]):  模型 state_dict 到 load. If  key 的  model_state_dict 是 nn.模块,  key 是  submodule 的 模型 和  value 应该 是  state_dict 的  submodule. When loading  state_dict,  prefix 的  submodule 将 是 append 到  state_dict.

options (StateDictOptions) –  options 到 control how 模型 state_dict 和 优化器 state_dict 应该 是 loaded. See StateDictOptions 用于  details.

missing_keys 是  list 的 str containing  missing keys unexpected_keys 是  list 的 str containing  unexpected keys

missing_keys 是  list 的 str containing  missing keys

unexpected_keys 是  list 的 str containing  unexpected keys

NamedTuple 使用 missing_keys 和 unexpected_keys fields

Load  optimizers state_dict.

 counterpart 的 get_optimizer_state_dict 到 set  state_dict 到  optimizers. See set_state_dict 用于  detail usage.

步骤() 是 called 在  optimizers. Otherwise,  优化器 states won’t 是 initialized correctly.

模型 (nn.模块) –  nn.模块 到  模型.

optimizers (Union[优化器, Iterable[优化器]]) –  optimizers that 是 used 到 optimize 模型.

optim_state_dict (OptimizerStateType) – OptimizerStateType:  优化器 state_dict 到 load.

options (StateDictOptions) –  options 到 control how 模型 state_dict 和 优化器 state_dict 应该 是 loaded. See StateDictOptions 用于  details.

This dataclass specifies how get_state_dict/set_state_dict 将 work.

full_state_dict: if this 是 set 到 True, all  tensors 在  returned state_dict 将 是 gathered. No ShardedTensor 和 DTensor 将 是 在  returned state_dict.

cpu_offload: offload all  tensors 到 CPU. 到 prevent CPU OOM, if full_state_dict 是 also true, then only  rank0 将 get  state_dict 和 all other ranks 将 get empty state_dict.

ignore_frozen_params: if  value 是 True,  returned state_dict won’t contain any frozen 参数 –  requires_grad 是 False.  default value 是 False.

keep_submodule_prefixes (deprecated): when submodules 是 not None, this option indicates whether 到 keep  submodule prefixes 从  state_dict keys. 或 示例, if  submodule 是 模块.pretrain 和  full FQN 的  参数 是 pretrain.layer1.权重 的  param. When this option 是 True,  参数’s key 在  returned state_dict 将 是 pretrain.layer1.权重. If  options 是 False,  key 将 是 layer1.权重. Note that if keep_submodule_prefixes 是 False, there 可能 是 conflicted FQNs, hence there 应该 是 only one submodule 在 submodules.

strict:  strict option when set_state_dict calls 模型.load_state_dict().

full state_dict 和 将 广播  tensors 在  state_dict/ optim_state_dict one 由 one 到 other ranks. Other ranks 将 receive  tensors 和 shard according 到  local shards 在  模型 和 优化器. full_state_dict 必须 是 set 到 True when using this option. This option currently only supports DTensor, not  legacy ShardedTensor.

用于 users which 是 used 到 using 和 sharing 模型 在  torch.save format,  following methods 是 provided which provide offline utilities 用于 converting betweeing formats.

Given  directory containing  DCP checkpoint, this 函数 将 convert it into  Torch save file.

dcp_checkpoint_dir (Union[str, PathLike]) – Directory containing  DCP checkpoint.

torch_save_path (Union[str, PathLike]) – Filename 到 store  converted Torch save file.

到 avoid OOM, it’s recommended 到 only run this 函数 在  single rank.

Given  location 的  torch save file, converts it into  DCP checkpoint.

torch_save_path (Union[str, PathLike]) – Filename 的  Torch save file.

dcp_checkpoint_dir (Union[str, PathLike]) – Directory 到 store  DCP checkpoint.

到 avoid OOM, it’s recommended 到 only run this 函数 在  single rank.

 following classes 可以 also 是 utilized 用于 online loading 和 resharding 的 模型 从  torch.save format.

StorageReader 用于 reading  Torch Save file. This reader 将 read  entire checkpoint 在  coordinator rank, 和 then 广播 和 shard each 张量 到 all ranks.

. N.B. Intended 到 是 used 使用 DynamicMetaLoadPlanner

Current 实现 only supports loading Tensors.

实现 的  StorageReader method

list[torch.分布式.checkpoint.planner.LoadPlan]

实现 的  StorageReader method

Reads torch save 数据 在  coordinator rank, 和 广播 afterwards this incurrs  通信 cost, but avoids having 到 load  entire checkpoint 在 each rank, hopefully preventing OOM issues

Extends  default StorageReader 到 support building  metadata file

实现 的  StorageReader method

实现 的  StorageReader method

实现 的  StorageReader method

Extension 的 DefaultLoadPlanner, which creates  new Metadata object based 在  passed 在 状态 dict, avoiding  need 到 read metadata 从 disk. This 是 useful when reading formats which don’t 有  metadata file, like Torch Save files.

. N.B. Intended 到 是 used 使用 BroadcastingTorchSaveReader

Current 实现 only supports loading Tensors.

Setups 的  planner, extnding default behavior 由 creating  Metadata object 从  状态 dict

 following experimental interfaces 是 provided 用于 improved observability 在 production environments:

---

## torch.分布式.张量#

**URL:** https://pytorch.org/docs/stable/分布式.张量.html

**Contents:**
- torch.分布式.张量#
- PyTorch DTensor (分布式 张量)#
  - DTensor Class APIs#
  - DeviceMesh as  分布式 communicator#
  - DTensor Placement Types#
- Different ways 到 create  DTensor#
  - Create DTensor 从  logical torch.张量#
  - DTensor Factory Functions#
  - Random 操作#
- Debugging#

Created 在: Jun 13, 2025 | Last Updated 在: Aug 23, 2025

torch.分布式.张量 是 currently 在 alpha 状态 和 under development, we 是 committing backward compatibility 用于  most APIs listed 在  doc, but there 可能 是 API changes if necessary.

PyTorch DTensor offers simple 和 flexible 张量 sharding primitives that transparently handles 分布式 logic, including sharded storage, operator 计算 和 collective communications across 设备/hosts. DTensor 可以 是 used 到 build different parallelism solutions 和 support sharded state_dict representation when working 使用 multi-dimensional sharding.

Please see examples 从  PyTorch native parallelism solutions that 是 built 在 top 的 DTensor:

DTensor follows  SPMD (single program, multiple 数据) programming 模型 到 empower users 到 write 分布式 program as if it’s  single-设备 program 使用  same convergence property. It provides  uniform 张量 sharding layout (DTensor Layout) through specifying  DeviceMesh 和 Placement:

DeviceMesh represents  设备 topology 和  communicators 的  cluster using  n-dimensional array.

Placement describes  sharding layout 的  logical 张量 在  DeviceMesh. DTensor supports three types 的 placements: Shard, Replicate 和 Partial.

DTensor 是  torch.张量 subclass. This means once  DTensor 是 created, it 可以 是 used 在 very similar way 到 torch.张量, including running different types 的 PyTorch operators as if running them 在  single 设备, allowing proper 分布式 计算 用于 PyTorch operators.

在 addition 到 existing torch.张量 methods, it also offers  set 的 additional methods 到 interact 使用 torch.张量, redistribute  DTensor Layout 到  new DTensor, get  full 张量 content 在 all 设备, etc.

DTensor (分布式 张量) 是  subclass 的 torch.张量 that provides single-设备 like abstraction 到 program 使用 multi-设备 torch.张量. It describes  分布式 张量 sharding layout (DTensor Layout) through  DeviceMesh 和 following types 的 Placement:

Shard: 张量 sharded 在  张量 dimension dim 在  设备 的  DeviceMesh dimension

Replicate: 张量 replicated 在  设备 的  DeviceMesh dimension

Partial: 张量 是 pending reduction 在  设备 的  DeviceMesh dimension

When calling PyTorch operators, DTensor overrides  PyTorch operators 到 perform sharded 计算 和 issue communications whenever necessary. Along 使用  operator 计算, DTensor 将 transform 或 propagate  placements (DTensor Layout) properly (based 在  operator semantic itself) 和 generate new DTensor outputs.

到 ensure numerical correctness 的  DTensor sharded 计算 when calling PyTorch operators, DTensor requires every 张量 argument 的  operator 是 DTensor.

Directly using  张量 subclass constructor here 是 not  recommended way 到 create  DTensor (i.e. it 做 not handle 自动求导 correctly hence 是 not  public API). Please refer 到  create_dtensor section 到 see how 到 create  DTensor.

Return  list 的 ChunkStorageMetadata, which 是  dataclass that describes  size/offset 的  local shard/副本 在 current rank. 用于 DTensor, each rank 将 有  single local shard/副本, so  returned list usually only 有 one element.

This dunder method 是 primariy used 用于 分布式 checkpoint purpose.

 List[ChunkStorageMetadata] object that represents  shard size/offset 在  current rank.

Create  DTensor 从  local torch.张量 在 each rank according 到  device_mesh 和 placements specified.

local_tensor (torch.张量) – local torch.张量 在 each rank.

device_mesh (DeviceMesh, optional) – DeviceMesh 到 place  张量, if not specified, 必须 是 called under  DeviceMesh context manager, default: None

placements (List[Placement], optional) –  placements that describes how 到 place  local torch.张量 在 DeviceMesh, 必须 有  same number 的 elements as device_mesh.ndim.

run_check (bool, optional) – at  cost 的 extra communications, perform sanity check across ranks 到 check each local 张量’s meta information 到 ensure correctness. If 有 Replicate 在 placements,  数据 在 first rank 的  设备 mesh dimension 将 是 broadcasted 到 other ranks. default: False

shape (torch.Size, optional) –  List 的 int which specifies  size 的 DTensor which build 在 top 的 local_tensor. Note this needs 到 是 provided if  shape 的 local_tensor 是 different across  ranks. If not provided, shape 将 是 computed assuming  given 分布式 张量 是 evenly sharded across ranks. default: None

stride (tuple, optional) –  List 的 int which specifies  stride 的 DTensor. If not provided, stride 将 是 computed assuming  given 分布式 张量 是 evenly sharded across ranks. default: None

When run_check=False, it 是  user’s responsibility 到 ensure  local 张量 passed 在 是 correct across ranks (i.e.  张量 是 sharded 用于  Shard(dim) placement 或 replicated 用于  Replicate() placement). If not,  behavior 的  created DTensor 是 undefined.

from_local 是 differentiable,  requires_grad 的  created DTensor object 将 depend 在 if local_tensor requires_grad 或 not.

Return  full 张量 的 this DTensor. It 将 perform necessary collectives 到 gather  local tensors 从 other ranks 在 its DeviceMesh 和 concatenate them together. It’s  syntactic sugar 的  following code:

dtensor.redistribute(placements=[Replicate()] * mesh.ndim).to_local()

grad_placements (List[Placement], optional) –  placements describes  future layout 的 any 梯度 layout 的  full 张量 returned 从 this 函数. full_tensor converts DTensor 到  full torch.张量 和  returned torch.张量 可能 not 是 used as  original replicated DTensor layout later 在  code. This argument 是  hint that user 可以 give 到 自动求导 在 case  梯度 layout 的  returned 张量 做 not match  original replicated DTensor layout. If not specified, we 将 assume  梯度 layout 的  full 张量 是 replicated.

 torch.张量 object that represents  full 张量 的 this DTensor.

full_tensor 是 differentiable.

redistribute performs necessary collective 操作 that redistribute  current DTensor 从 its current placements 到  new placements, 或 从 its current DeviceMesh 到  new DeviceMesh. i.e. we 可以 turn  Sharded DTensor 到  Replicated DTensor 由 specifying  Replicate placement 用于 each dimension 的  DeviceMesh.

When redistributing 从 current 到  new placements 在 one 设备 mesh dimension, we 将 perform  following 操作 including 通信 collective 或 local 操作:

Shard(dim) -> Replicate(): all_gather

Shard(src_dim) -> Shard(dst_dim): all_to_all

Replicate() -> Shard(dim): local chunking (i.e. torch.chunk)

Partial() -> Replicate(): all_reduce

Partial() -> Shard(dim): reduce_scatter

redistribute 将 correctly figure out  necessary redistribute steps 用于 DTensors that 是 created either 在 1-D 或 N-D DeviceMesh.

device_mesh (DeviceMesh, optional) – DeviceMesh 到 place  DTensor. If not specified, it 将 use  current DTensor’s DeviceMesh. default: None

placements (List[Placement], optional) –  new placements that describes how 到 place  DTensor into  DeviceMesh, 必须 有  same number 的 elements as device_mesh.ndim. default: replicate 在 all mesh dimensions

async_op (bool, optional) – whether 到 perform  DTensor redistribute 操作 asynchronously 或 not. Default: False

forward_dtype (torch.dtype, optional) –  local 张量 datatype 可以 是 converted 到 forward_dtype before redistributing  local 张量 在 its forward.  result DTensor 将 是 在 forward_dtype Default: None.

backward_dtype (torch.dtype, optional) –  local 张量 datatype 可以 是 converted 到 backward_dtype before redistributing  local 张量 在 its backward.  result DTensor 梯度 将 是 converted back 到  current DTensor dtype. Default: None

redistribute 是 differentiable, which means user 做 not need 到 worry about  backward formula 的  redistribute 操作.

redistribute currently only supports redistributing DTensor 在  same DeviceMesh, Please file  issue if you need 到 redistribute DTensor 到 different DeviceMesh.

Get  local 张量 的 this DTensor 在 its current rank. 用于 sharding it returns  local shard 的  logical 张量 view, 用于 replication it returns  副本 在 its current rank.

grad_placements (List[Placement], optional) –  placements describes  future layout 的 any 梯度 layout 的  张量 returned 从 this 函数. to_local converts DTensor 到 local 张量 和  returned local 张量 可能 not 是 used as  original DTensor layout later 在  code. This argument 是  hint that user 可以 give 到 自动求导 在 case  梯度 layout 的  returned 张量 做 not match  original DTensor layout. If not specified, we 将 assume  梯度 layout remains  same as  original DTensor 和 use that 用于 梯度 计算.

 torch.张量 或 AsyncCollectiveTensor object. it represents  local 张量 在 its current rank. When  AsyncCollectiveTensor object 是 returned, it means  local 张量 是 not ready yet (i.e. 通信 是 not finished). 在 this case, user needs 到 call wait 到 wait  local 张量 到 是 ready.

to_local 是 differentiable,  requires_grad 的  local 张量 returned 将 depend 在 if  DTensor requires_grad 或 not.

 DeviceMesh attribute that associates 使用 this DTensor object.

device_mesh 是  read-only property, it 可以 not 是 set.

 placements attribute 的 this DTensor that describes  layout 的 this DTensor 在  its DeviceMesh.

placements 是  read-only property, it 可以 not 是 set.

DeviceMesh 是 built 从 DTensor as  abstraction 到 describe cluster’s 设备 topology 和 represent multi-dimensional communicators (在 top 的 进程组). 到 see  details 的 how 到 create/use  DeviceMesh, please refer 到  DeviceMesh recipe.

DTensor supports  following types 的 Placement 在 each DeviceMesh dimension:

 Shard(dim) placement describes  DTensor sharding 在 张量 dimension dim over  corresponding DeviceMesh dimension, where each rank 在  DeviceMesh dimension only holds  shard/piece 的  global 张量.  Shard(dim) placement follows  torch.chunk(dim) semantic, where  last few shards 在  DeviceMesh dimension 可能 是 empty when  张量 dimension 是 not evenly divisible 在  DeviceMesh dimension.  Shard placement 可以 是 used 由 all DTensor APIs (i.e. distribute_tensor, from_local, etc.)

dim (int) –  张量 dimension that describes  DTensor 是 sharded over its corresponding DeviceMesh dimension.

sharding 在  张量 dimension where  张量 dimension size 是 not evenly divisible 在  DeviceMesh dimension 是 currently experimental 和 subject 到 change.

 Replicate() placement describes  DTensor replicating 在  corresponding DeviceMesh dimension, where each rank 在  DeviceMesh dimension holds  副本 的  global 张量.  Replicate placement 可以 是 used 由 all DTensor APIs (i.e. distribute_tensor, DTensor.from_local, etc.)

 Partial(reduce_op) placement describes  DTensor that 是 pending reduction 在  specified DeviceMesh dimension, where each rank 在  DeviceMesh dimension holds  partial value 的  global 张量. User 可以 redistribute  Partial DTensor 到  Replicate 或 Shard(dim) placement 在  specified DeviceMesh dimension using redistribute, which 将 trigger necessary 通信 操作 under  hood (i.e. 全归约, reduce_scatter).

reduce_op (str, optional) –  reduction op 到 是 used 用于  partial DTensor 到 produce Replicated/Sharded DTensor. Only element-wise reduction 操作 是 supported, including: “sum”, “avg”, “product”, “max”, “min”, default: “sum”.

 Partial placement 可以 是 generated as  result 的  DTensor operators, 和 可以 only 是 used 由  DTensor.from_local API.

 base class 用于  Placement type, where it describes how  DTensor 是 placed onto  DeviceMesh. Placement 和 DeviceMesh together 可以 describe  DTensor Layout. It 是  base class 的  three main DTensor Placement types: Shard, Replicate, 和 Partial.

This class 是 not meant 到 是 used directly, mainly served as  typing stub.

distribute_tensor() creates  DTensor 从  logical 或 “global” torch.张量 在 each rank. This 可以 是 used 到 shard  leaf torch.张量 s (i.e. 模型 参数/buffers 和 inputs).

DTensor.from_local() creates  DTensor 从  local torch.张量 在 each rank, which 可以 是 used 到 create DTensor 从  non-leaf torch.张量 s (i.e. intermediate 激活 tensors during forward/backward).

DTensor provides dedicated 张量 factory functions (e.g. empty(), ones(), randn(), etc.) 到 allow different DTensor creations 由 directly specifying  DeviceMesh 和 Placement. Compare 到 distribute_tensor(), this 可以 directly materializing  sharded 内存 在 设备, instead 的 performing sharding after initializing  logical 张量 内存.

 SPMD (single program, multiple 数据) programming 模型 在 torch.分布式 launches multiple 进程 (i.e. via torchrun) 到 execute  same program, this means that  模型 inside  program 将 是 initialized 在 different 进程 first (i.e.  模型 可能 是 initialized 在 CPU, 或 meta 设备, 或 directly 在 GPU if enough 内存).

DTensor offers  distribute_tensor() API that 可以 shard  模型 weights 或 Tensors 到 DTensor s, where it 将 create  DTensor 从  “logical” 张量 在 each 进程. This 将 empower  created DTensor s 到 comply 使用  single 设备 semantic, which 是 critical 用于 numerical correctness.

Distribute  leaf torch.张量 (i.e. nn.参数/buffers) 到  device_mesh according 到  placements specified.  rank 的 device_mesh 和 placements 必须 是  same.  张量 到 distribute 是  logical 或 “global” 张量, 和  API 将 use  张量 从 first rank 的  DeviceMesh dimension as  source 的 truth 到 preserve  single-设备 semantic. If you want 到 construct  DTensor 在  middle 的  自动求导 计算, please use DTensor.from_local() instead.

张量 (torch.张量) – torch.张量 到 是 分布式. Note that if you want 到 shard  张量 在  dimension that 是 not evenly divisible 由  number 的 设备 在 that mesh dimension, we use torch.chunk semantic 到 shard  张量 和 scatter  shards.  uneven sharding behavior 是 experimental 和 subject 到 change.

device_mesh (DeviceMesh, optional) – DeviceMesh 到 distribute  张量, if not specified, 必须 是 called under  DeviceMesh context manager, default: None

placements (List[Placement], optional) –  placements that describes how 到 place  张量 在 DeviceMesh, 必须 有  same number 的 elements as device_mesh.ndim. If not specified, we 将 由 default replicate  张量 across  device_mesh 从  first rank 的 each dimension 的  device_mesh.

src_data_rank (int, optional) –  rank 的  source 数据 用于  logical/global 张量, it 是 used 由 distribute_tensor() 到 scatter/广播  shards/副本 到 other ranks. 由 default, we use group_rank=0 在 each DeviceMesh dimension as  source 数据 到 preserve  single-设备 semantic. If passing None explicitly, distribute_tensor() simply uses its local 数据 instead 的 trying 到 preserve  single-设备 semantic via scatter/广播. Default: 0

 DTensor 或 XLAShardedTensor object.

When initialize  DeviceMesh 使用  xla device_type, distribute_tensor return XLAShardedTensor instead. see this issue 用于 more details.  XLA integration 是 experimental 和 subject 到 change.

Along 使用 distribute_tensor(), DTensor also offers  distribute_module() API 到 allow easier sharding 在  nn.模块 level

This 函数 expose three functions 到 control  参数/inputs/outputs 的  模块:

1. 到 perform sharding 在  模块 before runtime execution 由 specifying  partition_fn (i.e. allow user 到 convert 模块 参数 到 DTensor 参数 according 到  partition_fn specified). 2. 到 control  inputs 或 outputs 的  模块 during runtime execution 由 specifying  input_fn 和 output_fn. (i.e. convert  输入 到 DTensor, convert  输出 back 到 torch.张量)

模块 (nn.模块) – user 模块 到 是 partitioned.

device_mesh (DeviceMesh) –  设备 mesh 到 place  模块.

partition_fn (Callable) –  函数 到 partition 参数 (i.e. shard certain 参数 across  device_mesh). If partition_fn 是 not specified, 由 default we replicate all 模块 参数 的 模块 across  mesh.

input_fn (Callable) – specify  输入 distribution, i.e. 可以 control how  输入 的  模块 是 sharded. input_fn 将 是 installed as  模块 forward_pre_hook (pre forward 钩子).

output_fn (Callable) – specify  输出 distribution, i.e. 可以 control how  输出 是 sharded, 或 convert it back 到 torch.张量. output_fn 将 是 installed as  模块 forward_hook (post forward 钩子).

 模块 that contains 参数/buffers that 是 all DTensor s.

When initialize  DeviceMesh 使用  xla device_type, distribute_module return nn.模块 使用 PyTorch/XLA SPMD annotated 参数. See this issue 用于 more details.  XLA integration 是 experimental 和 subject 到 change.

DTensor also provides dedicated 张量 factory functions 到 allow creating DTensor directly using torch.张量 like factory 函数 APIs (i.e. torch.ones, torch.empty, etc), 由 additionally specifying  DeviceMesh 和 Placement 用于  DTensor created:

Returns  DTensor filled 使用  scalar value 0.

size (int...) –  序列 的 integers defining  shape 的  输出 DTensor. 可以 是  variable number 的 arguments 或  collection like  list 或 tuple. E.g.: zeros(1,2,3..) 或 zeros([1,2,3..]) 或 zeros((1,2,3..))

requires_grad (bool, optional) – If 自动求导 应该 record 操作 在  returned DTensor. Default: False.

dtype (torch.dtype, optional) –  desired 数据 type 的 returned DTensor. Default: if None, uses  global default (see torch.set_default_dtype()).

layout (torch.layout, optional) –  desired layout 的 returned DTensor. Default: torch.strided.

device_mesh – DeviceMesh type, contains  mesh info 的 ranks

placements –  序列 的 Placement type: Shard, Replicate

 DTensor object 在 each rank

Returns  DTensor filled 使用  scalar value 1, 使用  shape defined 由  variable argument size.

size (int...) –  序列 的 integers defining  shape 的  输出 DTensor. 可以 是  variable number 的 arguments 或  collection like  list 或 tuple. E.g.: ones(1,2,3..) 或 ones([1,2,3..]) 或 ones((1,2,3..))

dtype (torch.dtype, optional) –  desired 数据 type 的 returned DTensor. Default: if None, uses  global default (see torch.set_default_dtype()).

layout (torch.layout, optional) –  desired layout 的 returned DTensor. Default: torch.strided.

requires_grad (bool, optional) – If 自动求导 应该 record 操作 在  returned DTensor. Default: False.

device_mesh – DeviceMesh type, contains  mesh info 的 ranks

placements –  序列 的 Placement type: Shard, Replicate

 DTensor object 在 each rank

Returns  DTensor filled 使用 uninitialized 数据.  shape 的  DTensor 是 defined 由  variable argument size.

size (int...) –  序列 的 integers defining  shape 的  输出 DTensor. 可以 是  variable number 的 arguments 或  collection like  list 或 tuple. E.g.: empty(1,2,3..) 或 empty([1,2,3..]) 或 empty((1,2,3..))

dtype (torch.dtype, optional) –  desired 数据 type 的 returned DTensor. Default: if None, uses  global default (see torch.set_default_dtype()). layout (torch.layout, optional):  desired layout 的 returned DTensor. Default: torch.strided.

requires_grad (bool, optional) – If 自动求导 应该 record 操作 在  returned DTensor. Default: False.

device_mesh – DeviceMesh type, contains  mesh info 的 ranks

placements –  序列 的 Placement type: Shard, Replicate

 DTensor object 在 each rank

Returns  DTensor filled 使用 fill_value according 到 device_mesh 和 placements, 使用  shape defined 由  argument size.

size (int...) –  序列 的 integers defining  shape 的  输出 DTensor. 可以 是  variable number 的 arguments 或  collection like  list 或 tuple. E.g.: ones(1,2,3..) 或 ones([1,2,3..]) 或 ones((1,2,3..))

fill_value (Scalar) –  value 到 fill  输出 张量 使用.

dtype (torch.dtype, optional) –  desired 数据 type 的 returned DTensor. Default: if None, uses  global default (see torch.set_default_dtype()).

layout (torch.layout, optional) –  desired layout 的 returned DTensor. Default: torch.strided.

requires_grad (bool, optional) – If 自动求导 应该 record 操作 在  returned DTensor. Default: False.

device_mesh – DeviceMesh type, contains  mesh info 的 ranks.

placements –  序列 的 Placement type: Shard, Replicate

 DTensor object 在 each rank

Returns  DTensor filled 使用 random numbers 从  uniform distribution 在  interval [0, 1).  shape 的  张量 是 defined 由  variable argument size.

size (int...) –  序列 的 integers defining  shape 的  输出 DTensor. 可以 是  variable number 的 arguments 或  collection like  list 或 tuple. E.g.: ones(1,2,3..) 或 ones([1,2,3..]) 或 ones((1,2,3..))

dtype (torch.dtype, optional) –  desired 数据 type 的 returned DTensor. Default: if None, uses  global default (see torch.set_default_dtype()).

layout (torch.layout, optional) –  desired layout 的 returned DTensor. Default: torch.strided.

requires_grad (bool, optional) – If 自动求导 应该 record 操作 在  returned DTensor. Default: False.

device_mesh – DeviceMesh type, contains  mesh info 的 ranks.

placements –  序列 的 Placement type: Shard, Replicate

 DTensor object 在 each rank

Returns  DTensor filled 使用 random numbers 从  normal distribution 使用 mean 0 和 variance 1.  shape 的  张量 是 defined 由  variable argument size.

size (int...) –  序列 的 integers defining  shape 的  输出 DTensor. 可以 是  variable number 的 arguments 或  collection like  list 或 tuple. E.g.: ones(1,2,3..) 或 ones([1,2,3..]) 或 ones((1,2,3..))

dtype (torch.dtype, optional) –  desired 数据 type 的 returned DTensor. Default: if None, uses  global default (see torch.set_default_dtype()).

layout (torch.layout, optional) –  desired layout 的 returned DTensor. Default: torch.strided.

requires_grad (bool, optional) – If 自动求导 应该 record 操作 在  returned DTensor. Default: False.

device_mesh – DeviceMesh type, contains  mesh info 的 ranks.

placements –  序列 的 Placement type: Shard, Replicate

 DTensor object 在 each rank

DTensor provides 分布式 RNG functionality 到 ensure that random 操作 在 sharded tensors get unique values, 和 random 操作 在 replicated tensors get  same values. This system requires that all participating ranks (e.g. SPMD ranks) start out using  same generator 状态 before each dtensor random 操作 是 performed, 和 if this 是 true, it ensures they all end up at  same 状态 after each dtensor random 操作 completes. There 是 no 通信 performed during random 操作 到 synchronize RNG states.

Operators that accept  generator kwarg 将 utilize  user-passed generator, if passed, 或  default generator 用于  设备 otherwise. Whichever generator 是 used, it 将 是 advanced after  DTensor 操作. It 是 valid 到 use  same generator 用于 both DTensor 和 non-DTensor 操作, but care 必须 是 taken 到 ensure  non-DTensor 操作 advance  generator 状态 equally 在 all ranks if so.

When using DTensor together 使用 Pipeline Parallelism, ranks 用于 each pipeline stage 应该 use  distinct seed, 和 ranks within  pipeline stage 应该 use  same seed.

DTensor’s RNG infra 是 based 在  philox based RNG 算法, 和 supports any philox based backend (cuda, 和 other cuda-like 设备), but unfortunately 做 not yet support  CPU backend.

When launching  program, you 可以 turn 在 additional logging using  TORCH_LOGS environment variable 从 torch._logging :

TORCH_LOGS=+dtensor 将 display logging.DEBUG messages 和 all levels above it.

TORCH_LOGS=dtensor 将 display logging.INFO messages 和 above.

TORCH_LOGS=-dtensor 将 display logging.WARNING messages 和 above.

到 debug  program that applied DTensor, 和 understand more details about what collectives happened under  hood, DTensor provides  CommDebugMode:

CommDebugMode 是  context manager that counts  number 的 functional collectives within its context. It 做 this using  TorchDispatchMode.

Not all collectives 是 supported yet.

Generates detailed table displaying 操作 和 collective tracing information 在  模块 level. Amount 的 information 是 dependent 在 noise_level

prints 模块-level collective counts

prints dTensor 操作 not included 在 trivial 操作, 模块 information

prints 操作 not included 在 trivial 操作

prints all 操作

Creates json file used 到 build browser visual 0. prints 模块-level collective counts 1. prints dTensor 操作 not included 在 trivial 操作 2. prints 操作 not included 在 trivial 操作 3. prints all 操作

Returns  通信 counts as  dictionary.

 通信 counts as  dictionary.

dict[str, dict[str, Any]]

dict[str, dict[str, Any]]

Alternative 到 console CommDebugMode 输出, writes 到 file specified 由  user

到 visualize  sharding 的  DTensor that 有 less than 3 dimensions, DTensor provides visualize_sharding():

Visualizes sharding 在  terminal 用于 DTensor that 是 1D 或 2D.

This requires  tabulate package, 或 rich 和 matplotlib. No sharding info 将 是 printed 用于 empty tensors

DTensor also provides  set 的 experimental features. These features 是 either 在 prototyping stage, 或  basic functionality 是 done 和 but looking 用于 user feedbacks. Please submit  issue 到 PyTorch if you 有 feedbacks 到 these features.

context_parallel 是  experimental API 到 enable context parallelism (CP). This API performs two actions: 1) patch  SDPA (torch.nn.functional.scaled_dot_product_attention) 使用  CP-enabled one, 2) shard buffers along  序列 dimension 和 each rank 将 preserve  corresponding shard according mesh.

mesh (DeviceMesh) –  设备 mesh 用于  context parallelism.

buffers (Optional[List[torch.张量]]) – buffers that  usage depend 在  序列 dimension. Examples 是 输入 批次, labels 和 positional 嵌入 buffers. These buffers 必须 是 sharded along  序列 dimension 到 ensure  accuracy.  sharding 将 happen 在-place,  buffer’s shape 将 change within  context.  buffers 将 是 restored after  context finishes. no_restore_buffers 可以 是 used 到 specify which buffers don’t need 到 是 restored. Note that buffers 应该 not contain any nn.参数.

buffer_seq_dims (Optional[List[int]]) –  序列 dimensions 的 buffers.

no_restore_buffers (Optional[Set[torch.张量]]) – buffers 在 these set won’t 是 restored after  context exits. This set 必须 是  subset 的 buffers. If  buffers won’t 是 used after  context exits, these buffers 可以 是 put 在 this list 到 avoid extra restore time.

Generator[None, None, None]

torch.分布式.张量.experimental.context_parallel 是  prototype feature 在 PyTorch.  API 是 subject 到 change.

local_map() 是  experimental API that allows users 到 传播 DTensor s 到  函数 that 是 written 到 是 applied 在 torch.张量 s. It 是 done 由 extracting  local components 的 DTensor, call  函数, 和 wrap  outputs 到 DTensor according 到  out_placements.

func (Callable) –  函数 到 是 applied 在 each local shard 的 DTensor s.

out_placements (Union[PlacementType, Tuple[PlacementType, …]]) –  desired placements 的  DTensor s 在 func’s flattened 输出. If  flattened 输出 是  single value,  out_placements 应该 是 的 type PlacementType. Otherwise if  flattened 输出 有 multiple values,  out_placements 应该 是  tuple 的 PlacementType values 1:1 mapping 到  flattened 输出. Besides, 用于 张量 输出, we use PlacementType as its placements ( Tuple[Placement] value). 用于 non-张量 输出,  PlacementType 应该 是 None. Note that  only exception 是 when no DTensor argument 是 passed 在. 在 this case, even if out_placements 是 not None,  result 函数 应该 ignore  desired placements because  函数 是 not running 使用 DTensor s.

in_placements (Tuple[PlacementType, …], optional) –  required placements 的  DTensor s 在  flattened inputs 的 func. If in_placements 是 specified, local_map() 将 examine whether  placements 的 each DTensor argument 是  same as  required placements 或 not. If  placements 是 not  same 和 redistribute_inputs 是 False,  exception 将 是 raised. Otherwise if redistribute_inputs 是 True,  argument 将 是 first redistributed 到  required sharding placements before passing its local 张量 到 func.  only exception 是 when required placements 是 not None 和  argument 是  torch.张量. 在 this case,  placements examination 将 是 skipped 和  argument 将 是 directly passed 到 func. If in_placements 是 None, no placements examination 将 是 performed. Default: None

in_grad_placements (Tuple[PlacementType, …], optional) –  placements hint 的  DTensor s 梯度 corresponds 到  flattened 输入 DTensor. This argument 是  hint that user 可以 give 到 to_local() 在 case  梯度 layout 的  local 张量 输入 做 not match its DTensor 输入 layout. If not specified, we 将 assume  梯度 layout 的  local 张量 输入 remains  same as  original DTensor 输入 和 use that 用于 梯度 计算. Default: None.

device_mesh (DeviceMesh, optional) –  设备 mesh that  输出 DTensor s 是 placed 在. If not specified, this 将 是 inferred 从  first 输入 DTensor’s 设备 mesh. Default: None.

redistribute_inputs (bool, optional) –  bool value indicating whether 到 reshard  输入 DTensor s when their placements 是 different 从  required 输入 placements. If this value 是 False 和 some DTensor 输入 有  different placement,  exception 将 是 raised. Default: False.

 Callable that applies func 到 each local shard 的  输入 DTensor 和 returns  DTensor constructed 从  return value 的 func.

AssertionError – 用于 any non-DTensor 输出, we require its corresponding 输出 placement 在 out_placements 是 None.  AssertionError 将 是 raised if this 是 not  case.

ValueError – If redistribute_inputs=False but  输入 DTensor needs  redistribution according 到 in_placements.

This API 是 currently experimental 和 subject 到 change

register_sharding() 是  experimental API that allows users 到 register sharding strategies 用于  operator when  张量 inputs 和 outputs 是 DTensor. It 可以 是 useful when: (1) there doesn’t exist  default sharding strategy 用于 op, e.g. when op 是  custom operator that 是 not supported 由 DTensor; (2) when users 将 like 到 overwrite default sharding strategies 的 existing operators.

op (Union[OpOverload, List[OpOverload]]) –  op 或  list 的 ops 到 register  customized sharding 函数.

 函数 decorator which 可以 是 used 到 wrap  函数 that defines  sharding strategy 用于  operator specified 在 op.  defined sharding strategy 将 是 registered 到 DTensor 和 将 override  default sharding strategy if DTensor 有 already implemented  operator.  customized sharding 函数 takes  same inputs as  original op (except that if  arg 是  torch.张量, it 将 是 replaced 由  张量-like object that DTensor uses internally).  函数 应该 return  序列 的 2-tuples, each specifying acceptable 输出 placements 和 its corresponding 输入 placements.

This API 是 currently experimental 和 subject 到 change

---

## FullyShardedDataParallel#

**URL:** https://pytorch.org/docs/stable/fsdp.html

**Contents:**
- FullyShardedDataParallel#

Created 在: Feb 02, 2022 | Last Updated 在: Jun 11, 2025

 wrapper 用于 sharding 模块 参数 across 数据 并行 workers.

This 是 inspired 由 Xu et al. as well as  ZeRO Stage 3 从 DeepSpeed. FullyShardedDataParallel 是 commonly shortened 到 FSDP.

Using FSDP involves wrapping your 模块 和 then initializing your 优化器 after. This 是 required since FSDP changes  参数 variables.

When setting up FSDP, you need 到 consider  destination CUDA 设备. If  设备 有  ID (dev_id), you 有 three options:

Place  模块 在 that 设备

Set  设备 using torch.cuda.set_device(dev_id)

传播 dev_id into  device_id constructor argument.

This ensures that  FSDP instance’s compute 设备 是  destination 设备. 用于 option 1 和 3,  FSDP 初始化 always occurs 在 GPU. 用于 option 2,  FSDP 初始化 happens 在 模块’s current 设备, which 可能 是  CPU.

If you’re using  sync_module_states=True flag, you need 到 ensure that  模块 是 在  GPU 或 use  device_id argument 到 specify  CUDA 设备 that FSDP 将 move  模块 到 在  FSDP constructor. This 是 necessary because sync_module_states=True requires GPU 通信.

FSDP also takes care 的 moving 输入 tensors 到  forward method 到  GPU compute 设备, so you don’t need 到 manually move them 从 CPU.

用于 use_orig_params=True, ShardingStrategy.SHARD_GRAD_OP exposes  unsharded 参数, not  sharded 参数 after forward, unlike ShardingStrategy.FULL_SHARD. If you want 到 inspect  梯度, you 可以 use  summon_full_params method 使用 with_grads=True.

使用 limit_all_gathers=True, you 可能 see  gap 在  FSDP pre-forward where  CPU thread 是 not issuing any kernels. This 是 intentional 和 shows  rate limiter 在 effect. Synchronizing  CPU thread 在 that way prevents over-allocating 内存 用于 subsequent all-gathers, 和 it 应该 not actually delay GPU kernel execution.

FSDP replaces managed modules’ 参数 使用 torch.张量 views during forward 和 backward 计算 用于 自动求导-related reasons. If your 模块’s forward relies 在 saved references 到  参数 instead 的 reacquiring  references each 迭代, then it 将 not see FSDP’s newly created views, 和 自动求导 将 not work correctly.

Finally, when using sharding_strategy=ShardingStrategy.HYBRID_SHARD 使用  sharding 进程 group 正在 intra-node 和  replication 进程 group 正在 inter-node, setting NCCL_CROSS_NIC=1 可以 help improve  all-归约 times over  replication 进程 group 用于 some cluster setups.

There 是 several limitations 到 是 aware 的 when using FSDP:

FSDP currently 做 not support 梯度 accumulation outside no_sync() when using CPU offloading. This 是 because FSDP uses  newly-reduced 梯度 instead 的 accumulating 使用 any existing 梯度, which 可以 lead 到 incorrect results.

FSDP 做 not support running  前向传播 的  submodule that 是 contained 在  FSDP instance. This 是 because  submodule’s 参数 将 是 sharded, but  submodule itself 是 not  FSDP instance, so its 前向传播 将 not all-gather  full 参数 appropriately.

FSDP 做 not work 使用 double backwards due 到  way it registers backward 钩子.

FSDP 有 some constraints when freezing 参数. 用于 use_orig_params=False, each FSDP instance 必须 manage 参数 that 是 all frozen 或 all non-frozen. 用于 use_orig_params=True, FSDP supports mixing frozen 和 non-frozen 参数, but it’s recommended 到 avoid doing so 到 prevent higher than expected 梯度 内存 usage.

As 的 PyTorch 1.12, FSDP offers limited support 用于 shared 参数. If enhanced shared 参数 support 是 needed 用于 your use case, please post 在 this issue.

You 应该 avoid modifying  参数 between forward 和 backward without using  summon_full_params context, as  modifications 可能 not persist.

模块 (nn.模块) – This 是  模块 到 是 wrapped 使用 FSDP.

process_group (Optional[Union[进程组, Tuple[进程组, 进程组]]]) – This 是  进程 group over which  模型 是 sharded 和 thus  one used 用于 FSDP’s all-gather 和 归约-scatter collective communications. If None, then FSDP uses  default 进程 group. 用于 hybrid sharding strategies such as ShardingStrategy.HYBRID_SHARD, users 可以 传播 在  tuple 的 进程 groups, representing  groups over which 到 shard 和 replicate, respectively. If None, then FSDP constructs 进程 groups 用于  user 到 shard intra-node 和 replicate inter-node. (Default: None)

sharding_strategy (Optional[ShardingStrategy]) – This configures  sharding strategy, which 可能 trade off 内存 saving 和 通信 overhead. See ShardingStrategy 用于 details. (Default: FULL_SHARD)

cpu_offload (Optional[CPUOffload]) – This configures CPU offloading. If this 是 set 到 None, then no CPU offloading happens. See CPUOffload 用于 details. (Default: None)

auto_wrap_policy (Optional[Union[Callable[[nn.模块, bool, int], bool], ModuleWrapPolicy, CustomPolicy]]) – This specifies  policy 到 apply FSDP 到 submodules 的 模块, which 是 needed 用于 通信 和 计算 重叠 和 thus affects 性能. If None, then FSDP only applies 到 模块, 和 users 应该 manually apply FSDP 到 parent modules themselves (proceeding bottom-up). 用于 convenience, this accepts ModuleWrapPolicy directly, which allows users 到 specify  模块 classes 到 wrap (e.g.  变换器 block). Otherwise, this 应该 是  callable that takes 在 three arguments 模块: nn.模块, recurse: bool, 和 nonwrapped_numel: int 和 应该 return  bool specifying whether  passed-在 模块 应该 有 FSDP applied if recurse=False 或 if  traversal 应该 continue into  模块’s subtree if recurse=True. Users 可能 add additional arguments 到  callable.  size_based_auto_wrap_policy 在 torch.分布式.fsdp.wrap.py gives  示例 callable that applies FSDP 到  模块 if  参数 在 its subtree exceed 100M numel. We recommend printing  模型 after applying FSDP 和 adjusting as needed. 示例: >>> def custom_auto_wrap_policy( >>> 模块: nn.模块, >>> recurse: bool, >>> nonwrapped_numel: int, >>> # Additional custom arguments >>> min_num_params: int = int(1e8), >>> ) -> bool: >>> return nonwrapped_numel >= min_num_params >>> # Configure  custom `min_num_params` >>> my_auto_wrap_policy = functools.partial(custom_auto_wrap_policy, min_num_params=int(1e5))

This specifies  policy 到 apply FSDP 到 submodules 的 模块, which 是 needed 用于 通信 和 计算 重叠 和 thus affects 性能. If None, then FSDP only applies 到 模块, 和 users 应该 manually apply FSDP 到 parent modules themselves (proceeding bottom-up). 用于 convenience, this accepts ModuleWrapPolicy directly, which allows users 到 specify  模块 classes 到 wrap (e.g.  变换器 block). Otherwise, this 应该 是  callable that takes 在 three arguments 模块: nn.模块, recurse: bool, 和 nonwrapped_numel: int 和 应该 return  bool specifying whether  passed-在 模块 应该 有 FSDP applied if recurse=False 或 if  traversal 应该 continue into  模块’s subtree if recurse=True. Users 可能 add additional arguments 到  callable.  size_based_auto_wrap_policy 在 torch.分布式.fsdp.wrap.py gives  示例 callable that applies FSDP 到  模块 if  参数 在 its subtree exceed 100M numel. We recommend printing  模型 after applying FSDP 和 adjusting as needed.

backward_prefetch (Optional[BackwardPrefetch]) – This configures explicit backward prefetching 的 all-gathers. If None, then FSDP 做 not backward prefetch, 和 there 是 no 通信 和 计算 重叠 在  反向传播. See BackwardPrefetch 用于 details. (Default: BACKWARD_PRE)

mixed_precision (Optional[MixedPrecision]) – This configures native mixed precision 用于 FSDP. If this 是 set 到 None, then no mixed precision 是 used. Otherwise, 参数, buffer, 和 梯度 reduction dtypes 可以 是 set. See MixedPrecision 用于 details. (Default: None)

ignored_modules (Optional[Iterable[torch.nn.模块]]) – Modules whose own 参数 和 child modules’ 参数 和 buffers 是 ignored 由 this instance. None 的  modules directly 在 ignored_modules 应该 是 FullyShardedDataParallel instances, 和 any child modules that 是 already-constructed FullyShardedDataParallel instances 将 not 是 ignored if they 是 nested under this instance. This argument 可能 是 used 到 avoid sharding specific 参数 at 模块 granularity when using  auto_wrap_policy 或 if 参数’ sharding 是 not managed 由 FSDP. (Default: None)

param_init_fn (Optional[Callable[[nn.模块], None]]) –  Callable[torch.nn.模块] -> None that specifies how modules that 是 currently 在  meta 设备 应该 是 initialized onto  actual 设备. As 的 v1.12, FSDP detects modules 使用 参数 或 buffers 在 meta 设备 via is_meta 和 either applies param_init_fn if specified 或 calls nn.模块.reset_parameters() otherwise. 用于 both cases,  实现 应该 only initialize  参数/buffers 的  模块, not those 的 its submodules. This 是 到 avoid re-初始化. 在 addition, FSDP also supports deferred 初始化 via torchdistX’s (pytorch/torchdistX) deferred_init() API, where  deferred modules 是 initialized 由 calling param_init_fn if specified 或 torchdistX’s default materialize_module() otherwise. If param_init_fn 是 specified, then it 是 applied 到 all meta-设备 modules, meaning that it 应该 probably case 在  模块 type. FSDP calls  初始化 函数 before 参数 flattening 和 sharding. 示例: >>> 模块 = MyModule(设备="meta") >>> def my_init_fn(模块: nn.模块): >>> # E.g. initialize depending 在  模块 type >>> ... >>> fsdp_model = FSDP(模块, param_init_fn=my_init_fn, auto_wrap_policy=size_based_auto_wrap_policy) >>> print(next(fsdp_model.参数()).设备) # current CUDA 设备 >>> # 使用 torchdistX >>> 模块 = deferred_init.deferred_init(MyModule, 设备="cuda") >>> # 将 initialize via deferred_init.materialize_module(). >>> fsdp_model = FSDP(模块, auto_wrap_policy=size_based_auto_wrap_policy)

 Callable[torch.nn.模块] -> None that specifies how modules that 是 currently 在  meta 设备 应该 是 initialized onto  actual 设备. As 的 v1.12, FSDP detects modules 使用 参数 或 buffers 在 meta 设备 via is_meta 和 either applies param_init_fn if specified 或 calls nn.模块.reset_parameters() otherwise. 用于 both cases,  实现 应该 only initialize  参数/buffers 的  模块, not those 的 its submodules. This 是 到 avoid re-初始化. 在 addition, FSDP also supports deferred 初始化 via torchdistX’s (pytorch/torchdistX) deferred_init() API, where  deferred modules 是 initialized 由 calling param_init_fn if specified 或 torchdistX’s default materialize_module() otherwise. If param_init_fn 是 specified, then it 是 applied 到 all meta-设备 modules, meaning that it 应该 probably case 在  模块 type. FSDP calls  初始化 函数 before 参数 flattening 和 sharding.

device_id (Optional[Union[int, torch.设备]]) –  int 或 torch.设备 giving  CUDA 设备 在 which FSDP 初始化 takes place, including  模块 初始化 if needed 和  参数 sharding. This 应该 是 specified 到 improve 初始化 speed if 模块 是 在 CPU. If  default CUDA 设备 是 set (e.g. via torch.cuda.set_device), then  user 可能 传播 torch.cuda.current_device 到 this. (Default: None)

sync_module_states (bool) – If True, then each FSDP 模块 将 广播 模块 参数 和 buffers 从 rank 0 到 ensure that they 是 replicated across ranks (adding 通信 overhead 到 this constructor). This 可以 help load state_dict checkpoints via load_state_dict 在  内存 efficient way. See FullStateDictConfig 用于  示例 的 this. (Default: False)

forward_prefetch (bool) – If True, then FSDP explicitly prefetches  next forward-传播 all-gather before  current forward 计算. This 是 only useful 用于 CPU-bound workloads, 在 which case issuing  next all-gather earlier 可能 improve 重叠. This 应该 only 是 used 用于 static-图 模型 since  prefetching follows  first 迭代’s execution order. (Default: False)

limit_all_gathers (bool) – If True, then FSDP explicitly synchronizes  CPU thread 到 ensure GPU 内存 usage 从 only two consecutive FSDP instances ( current instance running 计算 和  next instance whose all-gather 是 prefetched). If False, then FSDP allows  CPU thread 到 issue all-gathers without any extra 同步. (Default: True) We often refer 到 this feature as  “rate limiter”. This flag 应该 only 是 set 到 False 用于 specific CPU-bound workloads 使用 low 内存 pressure 在 which case  CPU thread 可以 aggressively issue all kernels without concern 用于  GPU 内存 usage.

use_orig_params (bool) – Setting this 到 True 有 FSDP use 模块 ‘s original 参数. FSDP exposes those original 参数 到  user via nn.模块.named_parameters() instead 的 FSDP’s internal FlatParameter s. This means that  优化器步骤 runs 在  original 参数, enabling per-original-参数 hyperparameters. FSDP preserves  original 参数 variables 和 manipulates their 数据 between unsharded 和 sharded forms, where they 是 always views into  underlying unsharded 或 sharded FlatParameter, respectively. 使用  current 算法,  sharded form 是 always 1D, losing  original 张量 structure.  original 参数 可能 有 all, some, 或 none 的 its 数据 present 用于  given rank. 在  none case, its 数据 将 是 like  size-0 empty 张量. Users 应该 not author programs relying 在 what 数据 是 present 用于  given original 参数 在 its sharded form. True 是 required 到 use torch.compile(). Setting this 到 False exposes FSDP’s internal FlatParameter s 到  user via nn.模块.named_parameters(). (Default: False)

ignored_states (Optional[Iterable[torch.nn.参数]], Optional[Iterable[torch.nn.模块]]) – Ignored 参数 或 modules that 将 not 是 managed 由 this FSDP instance, meaning that  参数 是 not sharded 和 their 梯度 是 not reduced across ranks. This argument unifies 使用  existing ignored_modules argument, 和 we 可能 deprecate ignored_modules soon. 用于 backward compatibility, we keep both ignored_states 和 ignored_modules`, but FSDP only allows one 的 them 到 是 specified as not None.

device_mesh (Optional[DeviceMesh]) – DeviceMesh 可以 是 used as  alternative 到 process_group. When device_mesh 是 passed, FSDP 将 use  underlying 进程 groups 用于 all-gather 和 归约-scatter collective communications. Therefore, these two args need 到 是 mutually exclusive. 用于 hybrid sharding strategies such as ShardingStrategy.HYBRID_SHARD, users 可以 传播 在  2D DeviceMesh instead 的  tuple 的 进程 groups. 用于 2D FSDP + TP, users 是 required 到 传播 在 device_mesh instead 的 process_group. 用于 more DeviceMesh info, please visit: https://pytorch.org/tutorials/recipes/distributed_device_mesh.html

Apply fn recursively 到 every submodule (as returned 由 .children()) as well as self.

Typical use includes initializing  参数 的  模型 (see also torch.nn.init).

Compared 到 torch.nn.模块.apply, this version additionally gathers  full 参数 before applying fn. It 应该 not 是 called 从 within another summon_full_params context.

fn (模块 -> None) – 函数 到 是 applied 到 each submodule

Check if this instance 是  root FSDP 模块.

Clip  梯度 norm 的 all 参数.

 norm 是 computed over all 参数’ 梯度 as viewed as  single vector, 和  梯度 是 modified 在-place.

max_norm (float 或 int) – max norm 的  梯度

norm_type (float 或 int) – type 的  used p-norm. 可以 是 'inf' 用于 infinity norm.

Total norm 的  参数 (viewed as  single vector).

If every FSDP instance uses NO_SHARD, meaning that no 梯度 是 sharded across ranks, then you 可能 directly use torch.nn.utils.clip_grad_norm_().

If at least some FSDP instance uses  sharded strategy (i.e. one other than NO_SHARD), then you 应该 use this method instead 的 torch.nn.utils.clip_grad_norm_() since this method handles  fact that 梯度 是 sharded across ranks.

 total norm returned 将 有  “largest” dtype across all 参数/梯度 as defined 由 PyTorch’s type promotion semantics. 用于 示例, if all 参数/梯度 use  low precision dtype, then  returned norm’s dtype 将 是 that low precision dtype, but if there exists at least one 参数/ 梯度 using FP32, then  returned norm’s dtype 将 是 FP32.

This needs 到 是 called 在 all ranks since it uses collective communications.

Flatten  sharded 优化器 状态-dict.

 API 是 similar 到 shard_full_optim_state_dict().  only difference 是 that  输入 sharded_optim_state_dict 应该 是 returned 从 sharded_optim_state_dict(). Therefore, there 将 是 all-gather calls 在 each rank 到 gather ShardedTensor s.

sharded_optim_state_dict (Dict[str, Any]) – 优化器 状态 dict corresponding 到  unflattened 参数 和 holding  sharded 优化器 状态.

模型 (torch.nn.模块) – Refer 到 shard_full_optim_state_dict().

optim (torch.optim.优化器) – 优化器 用于 模型 ‘s 参数.

Refer 到 shard_full_optim_state_dict().

Run  前向传播 用于  wrapped 模块, inserting FSDP-specific pre- 和 post-forward sharding logic.

Return all nested FSDP instances.

This possibly includes 模块 itself 和 only includes FSDP root modules if root_only=True.

模块 (torch.nn.模块) – Root 模块, which 可能 或 可能 not 是  FSDP 模块.

root_only (bool) – Whether 到 return only FSDP root modules. (Default: False)

FSDP modules that 是 nested 在  输入 模块.

List[FullyShardedDataParallel]

Return  full 优化器 状态-dict.

Consolidates  full 优化器 状态 在 rank 0 和 returns it as  dict following  convention 的 torch.optim.优化器.state_dict(), i.e. 使用 keys "状态" 和 "param_groups".  flattened 参数 在 FSDP modules contained 在 模型 是 mapped back 到 their unflattened 参数.

This needs 到 是 called 在 all ranks since it uses collective communications. However, if rank0_only=True, then  状态 dict 是 only populated 在 rank 0, 和 all other ranks return  empty dict.

Unlike torch.optim.优化器.state_dict(), this method uses full 参数 names as keys instead 的 参数 IDs.

Like 在 torch.optim.优化器.state_dict(),  tensors contained 在  优化器 状态 dict 是 not cloned, so there 可能 是 aliasing surprises. 用于 best practices, consider saving  returned 优化器 状态 dict immediately, e.g. using torch.save().

模型 (torch.nn.模块) – Root 模块 (which 可能 或 可能 not 是  FullyShardedDataParallel instance) whose 参数 是 passed into  优化器 optim.

optim (torch.optim.优化器) – 优化器 用于 模型 ‘s 参数.

optim_input (Optional[Union[List[Dict[str, Any]], Iterable[torch.nn.参数]]]) – 输入 passed into  优化器 optim representing either  list 的 参数 groups 或  iterable 的 参数; if None, then this method assumes  输入 是 模型.参数(). This argument 是 deprecated, 和 there 是 no need 到 传播 it 在 anymore. (Default: None)

rank0_only (bool) – If True, saves  populated dict only 在 rank 0; if False, saves it 在 all ranks. (Default: True)

group (dist.进程组) – 模型’s 进程 group 或 None if using  default 进程 group. (Default: None)

 dict containing  优化器 状态 用于 模型 ‘s original unflattened 参数 和 including keys “状态” 和 “param_groups” following  convention 的 torch.optim.优化器.state_dict(). If rank0_only=True, then nonzero ranks return  empty dict.

Get  state_dict_type 和  corresponding configurations 用于  FSDP modules rooted at 模块.

 target 模块 做 not 有 到 是  FSDP 模块.

 StateDictSettings containing  state_dict_type 和 state_dict / optim_state_dict configs that 是 currently set.

AssertionError` if  StateDictSettings 用于 different –

FSDP submodules differ. –

Return  wrapped 模块.

Return  iterator over 模块 buffers, yielding both  name 的  buffer 和  buffer itself.

Intercepts buffer names 和 removes all occurrences 的  FSDP-specific flattened buffer prefix when inside  summon_full_params() context manager.

Iterator[tuple[str, torch.张量]]

Return  iterator over 模块 参数, yielding both  name 的  参数 和  参数 itself.

Intercepts 参数 names 和 removes all occurrences 的  FSDP-specific flattened 参数 prefix when inside  summon_full_params() context manager.

Iterator[tuple[str, torch.nn.参数.参数]]

Disable 梯度 synchronizations across FSDP instances.

Within this context, 梯度 将 是 accumulated 在 模块 variables, which 将 later 是 synchronized 在  first forward-反向传播 after exiting  context. This 应该 only 是 used 在  root FSDP instance 和 将 recursively apply 到 all children FSDP instances.

This likely results 在 higher 内存 usage because FSDP 将 accumulate  full 模型 梯度 (instead 的 梯度 shards) until  eventual sync.

When used 使用 CPU offloading,  梯度 将 not 是 offloaded 到 CPU when inside  context manager. Instead, they 将 only 是 offloaded right after  eventual sync.

Transform  状态-dict 的  优化器 corresponding 到  sharded 模型.

 given 状态-dict 可以 是 transformed 到 one 的 three types: 1) full 优化器 state_dict, 2) sharded 优化器 state_dict, 3) local 优化器 state_dict.

用于 full 优化器 state_dict, all states 是 unflattened 和 not sharded. Rank0 only 和 CPU only 可以 是 specified via state_dict_type() 到 avoid OOM.

用于 sharded 优化器 state_dict, all states 是 unflattened but sharded. CPU only 可以 是 specified via state_dict_type() 到 further save 内存.

用于 local state_dict, no transformation 将 是 performed. But  状态 将 是 converted 从 nn.张量 到 ShardedTensor 到 represent its sharding nature (this 是 not supported yet).

模型 (torch.nn.模块) – Root 模块 (which 可能 或 可能 not 是  FullyShardedDataParallel instance) whose 参数 是 passed into  优化器 optim.

optim (torch.optim.优化器) – 优化器 用于 模型 ‘s 参数.

optim_state_dict (Dict[str, Any]) –  target 优化器 state_dict 到 transform. If  value 是 None, optim.state_dict() 将 是 used. ( Default: None)

group (dist.进程组) – 模型’s 进程 group across which 参数 是 sharded 或 None if using  default 进程 group. ( Default: None)

 dict containing  优化器 状态 用于 模型.  sharding 的  优化器 状态 是 based 在 state_dict_type.

Convert  优化器 状态-dict so that it 可以 是 loaded into  优化器 associated 使用  FSDP 模型.

Given  optim_state_dict that 是 transformed through optim_state_dict(), it gets converted 到  flattened 优化器 state_dict that 可以 是 loaded 到 optim which 是  优化器 用于 模型. 模型 必须 是 sharded 由 FullyShardedDataParallel.

模型 (torch.nn.模块) – Root 模块 (which 可能 或 可能 not 是  FullyShardedDataParallel instance) whose 参数 是 passed into  优化器 optim.

optim (torch.optim.优化器) – 优化器 用于 模型 ‘s 参数.

optim_state_dict (Dict[str, Any]) –  优化器 states 到 是 loaded.

is_named_optimizer (bool) – 是 this 优化器  NamedOptimizer 或 KeyedOptimizer. Only set 到 True if optim 是 TorchRec’s KeyedOptimizer 或 torch.分布式’s NamedOptimizer.

load_directly (bool) – If this 是 set 到 True, this API 将 also call optim.load_state_dict(result) before returning  result. Otherwise, users 是 responsible 到 call optim.load_state_dict() (Default: False)

group (dist.进程组) – 模型’s 进程 group across which 参数 是 sharded 或 None if using  default 进程 group. ( Default: None)

Register  通信 钩子.

This 是  enhancement that provides  flexible 钩子 到 users where they 可以 specify how FSDP aggregates 梯度 across multiple workers. This 钩子 可以 是 used 到 implement several algorithms like GossipGrad 和 梯度 compression which involve different 通信 strategies 用于 参数 syncs while 训练 使用 FullyShardedDataParallel.

FSDP 通信 钩子 应该 是 registered before running  initial 前向传播 和 only once.

状态 (object) – Passed 到  钩子 到 maintain any 状态 information during  训练 进程. Examples include error feedback 在 梯度 compression, peers 到 communicate 使用 next 在 GossipGrad, etc. It 是 locally stored 由 each worker 和 shared 由 all  梯度 tensors 在  worker.

Passed 到  钩子 到 maintain any 状态 information during  训练 进程. Examples include error feedback 在 梯度 compression, peers 到 communicate 使用 next 在 GossipGrad, etc. It 是 locally stored 由 each worker 和 shared 由 all  梯度 tensors 在  worker.

钩子 (Callable) – Callable, which 有 one 的  following signatures: 1) 钩子: Callable[torch.张量] -> None: This 函数 takes 在  Python 张量, which represents  full, flattened, unsharded 梯度 使用 respect 到 all variables corresponding 到  模型 this FSDP unit 是 wrapping (that 是 not wrapped 由 other FSDP sub-units). It then performs all necessary processing 和 returns None; 2) 钩子: Callable[torch.张量, torch.张量] -> None: This 函数 takes 在 two Python tensors,  first one represents  full, flattened, unsharded 梯度 使用 respect 到 all variables corresponding 到  模型 this FSDP unit 是 wrapping (that 是 not wrapped 由 other FSDP sub-units).  latter represents  pre-sized 张量 到 store  chunk 的  sharded 梯度 after reduction. 在 both cases, callable performs all necessary processing 和 returns None. Callables 使用 signature 1 是 expected 到 handle 梯度 通信 用于  NO_SHARD case. Callables 使用 signature 2 是 expected 到 handle 梯度 通信 用于 sharded cases.

Re-keys  优化器 状态 dict optim_state_dict 到 use  key type optim_state_key_type.

This 可以 是 used 到 achieve compatibility between 优化器 状态 dicts 从 模型 使用 FSDP instances 和 ones without.

到 re-key  FSDP full 优化器 状态 dict (i.e. 从 full_optim_state_dict()) 到 use 参数 IDs 和 是 loadable 到  non-wrapped 模型:

到 re-key  normal 优化器 状态 dict 从  non-wrapped 模型 到 是 loadable 到  wrapped 模型:

 优化器 状态 dict re-keyed using  参数 keys specified 由 optim_state_key_type.

Scatter  full 优化器 状态 dict 从 rank 0 到 all other ranks.

Returns  sharded 优化器 状态 dict 在 each rank.  return value 是  same as shard_full_optim_state_dict(), 和 在 rank 0,  first argument 应该 是  return value 的 full_optim_state_dict().

Both shard_full_optim_state_dict() 和 scatter_full_optim_state_dict() 可能 是 used 到 get  sharded 优化器 状态 dict 到 load. Assuming that  full 优化器 状态 dict resides 在 CPU 内存,  former requires each rank 到 有  full dict 在 CPU 内存, where each rank individually shards  dict without any 通信, while  latter requires only rank 0 到 有  full dict 在 CPU 内存, where rank 0 moves each shard 到 GPU 内存 (用于 NCCL) 和 communicates it 到 ranks appropriately. Hence,  former 有 higher aggregate CPU 内存 cost, while  latter 有 higher 通信 cost.

full_optim_state_dict (Optional[Dict[str, Any]]) – 优化器 状态 dict corresponding 到  unflattened 参数 和 holding  full non-sharded 优化器 状态 if 在 rank 0;  argument 是 ignored 在 nonzero ranks.

模型 (torch.nn.模块) – Root 模块 (which 可能 或 可能 not 是  FullyShardedDataParallel instance) whose 参数 correspond 到  优化器 状态 在 full_optim_state_dict.

optim_input (Optional[Union[List[Dict[str, Any]], Iterable[torch.nn.参数]]]) – 输入 passed into  优化器 representing either  list 的 参数 groups 或  iterable 的 参数; if None, then this method assumes  输入 是 模型.参数(). This argument 是 deprecated, 和 there 是 no need 到 传播 it 在 anymore. (Default: None)

optim (Optional[torch.optim.优化器]) – 优化器 that 将 load  状态 dict returned 由 this method. This 是  preferred argument 到 use over optim_input. (Default: None)

group (dist.进程组) – 模型’s 进程 group 或 None if using  default 进程 group. (Default: None)

 full 优化器 状态 dict now remapped 到 flattened 参数 instead 的 unflattened 参数 和 restricted 到 only include this rank’s part 的  优化器 状态.

Set  state_dict_type 的 all  descendant FSDP modules 的  target 模块.

Also takes (optional) 配置 用于  模型’s 和 优化器’s 状态 dict.  target 模块 做 not 有 到 是  FSDP 模块. If  target 模块 是  FSDP 模块, its state_dict_type 将 also 是 changed.

This API 应该 是 called 用于 only  top-level (root) 模块.

This API enables users 到 transparently use  conventional state_dict API 到 take 模型 checkpoints 在 cases where  root FSDP 模块 是 wrapped 由 another nn.模块. 用于 示例,  following 将 ensure state_dict 是 called 在 all non-FSDP instances, while dispatching into sharded_state_dict 实现 用于 FSDP:

模块 (torch.nn.模块) – Root 模块.

state_dict_type (StateDictType) –  desired state_dict_type 到 set.

state_dict_config (Optional[StateDictConfig]) –  配置 用于  target state_dict_type.

optim_state_dict_config (Optional[OptimStateDictConfig]) –  配置 用于  优化器 状态 dict.

 StateDictSettings that include  previous state_dict type 和 配置 用于  模块.

Shard  full 优化器 状态-dict.

Remaps  状态 在 full_optim_state_dict 到 flattened 参数 instead 的 unflattened 参数 和 restricts 到 only this rank’s part 的  优化器 状态.  first argument 应该 是  return value 的 full_optim_state_dict().

Both shard_full_optim_state_dict() 和 scatter_full_optim_state_dict() 可能 是 used 到 get  sharded 优化器 状态 dict 到 load. Assuming that  full 优化器 状态 dict resides 在 CPU 内存,  former requires each rank 到 有  full dict 在 CPU 内存, where each rank individually shards  dict without any 通信, while  latter requires only rank 0 到 有  full dict 在 CPU 内存, where rank 0 moves each shard 到 GPU 内存 (用于 NCCL) 和 communicates it 到 ranks appropriately. Hence,  former 有 higher aggregate CPU 内存 cost, while  latter 有 higher 通信 cost.

full_optim_state_dict (Dict[str, Any]) – 优化器 状态 dict corresponding 到  unflattened 参数 和 holding  full non-sharded 优化器 状态.

模型 (torch.nn.模块) – Root 模块 (which 可能 或 可能 not 是  FullyShardedDataParallel instance) whose 参数 correspond 到  优化器 状态 在 full_optim_state_dict.

optim_input (Optional[Union[List[Dict[str, Any]], Iterable[torch.nn.参数]]]) – 输入 passed into  优化器 representing either  list 的 参数 groups 或  iterable 的 参数; if None, then this method assumes  输入 是 模型.参数(). This argument 是 deprecated, 和 there 是 no need 到 传播 it 在 anymore. (Default: None)

optim (Optional[torch.optim.优化器]) – 优化器 that 将 load  状态 dict returned 由 this method. This 是  preferred argument 到 use over optim_input. (Default: None)

 full 优化器 状态 dict now remapped 到 flattened 参数 instead 的 unflattened 参数 和 restricted 到 only include this rank’s part 的  优化器 状态.

Return  优化器 状态-dict 在 its sharded form.

 API 是 similar 到 full_optim_state_dict() but this API chunks all non-zero-dimension states 到 ShardedTensor 到 save 内存. This API 应该 only 是 used when  模型 state_dict 是 derived 使用  context manager 使用 state_dict_type(SHARDED_STATE_DICT):.

用于  detailed usage, refer 到 full_optim_state_dict().

 returned 状态 dict contains ShardedTensor 和 cannot 是 directly used 由  regular optim.load_state_dict.

Set  state_dict_type 的 all  descendant FSDP modules 的  target 模块.

This context manager 有  same functions as set_state_dict_type(). Read  document 的 set_state_dict_type() 用于  detail.

模块 (torch.nn.模块) – Root 模块.

state_dict_type (StateDictType) –  desired state_dict_type 到 set.

state_dict_config (Optional[StateDictConfig]) –  模型 state_dict 配置 用于  target state_dict_type.

optim_state_dict_config (Optional[OptimStateDictConfig]) –  优化器 state_dict 配置 用于  target state_dict_type.

Expose full params 用于 FSDP instances 使用 this context manager.

可以 是 useful after forward/backward 用于  模型 到 get  params 用于 additional processing 或 checking. It 可以 take  non-FSDP 模块 和 将 summon full params 用于 all contained FSDP modules as well as their children, depending 在  recurse argument.

This 可以 是 used 在 inner FSDPs.

This 可以 not 是 used within  forward 或 反向传播. Nor 可以 forward 和 backward 是 started 从 within this context.

参数 将 revert 到 their local shards after  context manager exits, storage behavior 是  same as forward.

 full 参数 可以 是 modified, but only  portion corresponding 到  local param shard 将 persist after  context manager exits (unless writeback=False, 在 which case changes 将 是 discarded). 在  case where FSDP 做 not shard  参数, currently only when world_size == 1, 或 NO_SHARD config,  modification 是 persisted regardless 的 writeback.

This method works 在 modules which 是 not FSDP themselves but 可能 contain multiple independent FSDP units. 在 that case,  given arguments 将 apply 到 all contained FSDP units.

Note that rank0_only=True 在 conjunction 使用 writeback=True 是 not currently supported 和 将 raise  error. This 是 because 模型 参数 shapes 将 是 different across ranks within  context, 和 writing 到 them 可以 lead 到 inconsistency across ranks when  context 是 exited.

Note that offload_to_cpu 和 rank0_only=False 将 result 在 full 参数 正在 redundantly copied 到 CPU 内存 用于 GPUs that reside 在  same machine, which 可能 incur  risk 的 CPU OOM. It 是 recommended 到 use offload_to_cpu 使用 rank0_only=True.

recurse (bool, Optional) – recursively summon all params 用于 nested FSDP instances (default: True).

writeback (bool, Optional) – if False, modifications 到 params 是 discarded after  context manager exits; disabling this 可以 是 slightly more efficient (default: True)

rank0_only (bool, Optional) – if True, full 参数 是 materialized 在 only global rank 0. This means that within  context, only rank 0 将 有 full 参数 和  other ranks 将 有 sharded 参数. Note that setting rank0_only=True 使用 writeback=True 是 not supported, as 模型 参数 shapes 将 是 different across ranks within  context, 和 writing 到 them 可以 lead 到 inconsistency across ranks when  context 是 exited.

offload_to_cpu (bool, Optional) – If True, full 参数 是 offloaded 到 CPU. Note that this offloading currently only occurs if  参数 是 sharded (which 是 only not  case 用于 world_size = 1 或 NO_SHARD config). It 是 recommended 到 use offload_to_cpu 使用 rank0_only=True 到 avoid redundant copies 的 模型 参数 正在 offloaded 到  same CPU 内存.

with_grads (bool, Optional) – If True, 梯度 是 also unsharded 使用  参数. Currently, this 是 only supported when passing use_orig_params=True 到  FSDP constructor 和 offload_to_cpu=False 到 this method. (Default: False)

This configures explicit backward prefetching, which improves throughput 由 enabling 通信 和 计算 重叠 在  反向传播 at  cost 的 slightly increased 内存 usage.

BACKWARD_PRE: This enables  most 重叠 but increases 内存 usage  most. This prefetches  next set 的 参数 before  current set 的 参数’ 梯度 计算. This overlaps  next all-gather 和  current 梯度 计算, 和 at  peak, it holds  current set 的 参数, next set 的 参数, 和 current set 的 梯度 在 内存.

BACKWARD_POST: This enables less 重叠 but requires less 内存 usage. This prefetches  next set 的 参数 after  current set 的 参数’ 梯度 计算. This overlaps  current 归约-scatter 和  next 梯度 计算, 和 it frees  current set 的 参数 before allocating 内存 用于  next set 的 参数, only holding  next set 的 参数 和 current set 的 梯度 在 内存 at  peak.

FSDP’s backward_prefetch argument accepts None, which disables  backward prefetching altogether. This 有 no 重叠 和 做 not increase 内存 usage. 在 general, we 做 not recommend this setting since it 可能 degrade throughput significantly.

用于 more technical context: 用于  single 进程 group using NCCL backend, any collectives, even if issued 从 different streams, contend 用于  same per-设备 NCCL stream, which implies that  relative order 在 which  collectives 是 issued matters 用于 overlapping.  two backward prefetching values correspond 到 different issue orders.

This specifies  sharding strategy 到 是 used 用于 分布式 训练 由 FullyShardedDataParallel.

FULL_SHARD: 参数, 梯度, 和 优化器 states 是 sharded. 用于  参数, this strategy unshards (via all-gather) before  forward, reshards after  forward, unshards before  backward 计算, 和 reshards after  backward 计算. 用于 梯度, it synchronizes 和 shards them (via 归约-scatter) after  backward 计算.  sharded 优化器 states 是 updated locally per rank.

SHARD_GRAD_OP: 梯度 和 优化器 states 是 sharded during 计算, 和 additionally, 参数 是 sharded outside 计算. 用于  参数, this strategy unshards before  forward, 做 not reshard them after  forward, 和 only reshards them after  backward 计算.  sharded 优化器 states 是 updated locally per rank. Inside no_sync(),  参数 是 not resharded after  backward 计算.

NO_SHARD: 参数, 梯度, 和 优化器 states 是 not sharded but instead replicated across ranks similar 到 PyTorch’s 分布式数据并行 API. 用于 梯度, this strategy synchronizes them (via all-归约) after  backward 计算.  unsharded 优化器 states 是 updated locally per rank.

HYBRID_SHARD: Apply FULL_SHARD within  node, 和 replicate 参数 across nodes. This results 在 reduced 通信 volume as expensive all-gathers 和 归约-scatters 是 only done within  node, which 可以 是 more performant 用于 medium -sized 模型.

_HYBRID_SHARD_ZERO2: Apply SHARD_GRAD_OP within  node, 和 replicate 参数 across nodes. This 是 like HYBRID_SHARD, except this 可能 provide even higher throughput since  unsharded 参数 是 not freed after  前向传播, saving  all-gathers 在  pre-backward.

This configures FSDP-native mixed precision 训练.

param_dtype (Optional[torch.dtype]) – This specifies  dtype 用于 模型 参数 during forward 和 backward 和 thus  dtype 用于 forward 和 backward 计算. Outside forward 和 backward,  sharded 参数 是 kept 在 full precision (e.g. 用于  优化器步骤), 和 用于 模型 checkpointing,  参数 是 always saved 在 full precision. (Default: None)

reduce_dtype (Optional[torch.dtype]) – This specifies  dtype 用于 梯度 reduction (i.e. 归约-scatter 或 all-归约). If this 是 None but param_dtype 是 not None, then this takes 在  param_dtype value, still running 梯度 reduction 在 low precision. This 是 permitted 到 differ 从 param_dtype, e.g. 到 force 梯度 reduction 到 run 在 full precision. (Default: None)

buffer_dtype (Optional[torch.dtype]) – This specifies  dtype 用于 buffers. FSDP 做 not shard buffers. Rather, FSDP casts them 到 buffer_dtype 在  first 前向传播 和 keeps them 在 that dtype thereafter. 用于 模型 checkpointing,  buffers 是 saved 在 full precision except 用于 LOCAL_STATE_DICT. (Default: None)

keep_low_precision_grads (bool) – If False, then FSDP upcasts 梯度 到 full precision after  反向传播 在 preparation 用于  优化器步骤. If True, then FSDP keeps  梯度 在  dtype used 用于 梯度 reduction, which 可以 save 内存 if using  custom 优化器 that supports running 在 low precision. (Default: False)

cast_forward_inputs (bool) – If True, then this FSDP 模块 casts its forward args 和 kwargs 到 param_dtype. This 是 到 ensure that 参数 和 输入 dtypes match 用于 forward 计算, as required 由 many ops. This 可能 need 到 是 set 到 True when only applying mixed precision 到 some but not all FSDP modules, 在 which case  mixed-precision FSDP submodule needs 到 recast its inputs. (Default: False)

cast_root_forward_inputs (bool) – If True, then  root FSDP 模块 casts its forward args 和 kwargs 到 param_dtype, overriding  value 的 cast_forward_inputs. 用于 non-root FSDP modules, this 做 not 做 anything. (Default: True)

_module_classes_to_ignore (collections.abc.序列[type[torch.nn.modules.模块.模块]]) – (序列[Type[nn.模块]]): This specifies 模块 classes 到 ignore 用于 mixed precision when using  auto_wrap_policy: Modules 的 these classes 将 有 FSDP applied 到 them separately 使用 mixed precision disabled (meaning that  final FSDP 构造 将 deviate 从  specified policy). If auto_wrap_policy 是 not specified, then this 做 not 做 anything. This API 是 experimental 和 subject 到 change. (Default: (_BatchNorm,))

This API 是 experimental 和 subject 到 change.

Only floating point tensors 是 cast 到 their specified dtypes.

在 summon_full_params, 参数 是 forced 到 full precision, but buffers 是 not.

层 norm 和 批次 norm accumulate 在 float32 even when their inputs 是 在  low precision like float16 或 bfloat16. Disabling FSDP’s mixed precision 用于 those norm modules only means that  affine 参数 是 kept 在 float32. However, this incurs separate all-gathers 和 归约-scatters 用于 those norm modules, which 可能 是 inefficient, so if  workload permits,  user 应该 prefer 到 still apply mixed precision 到 those modules.

由 default, if  user passes  模型 使用 any _BatchNorm modules 和 specifies  auto_wrap_policy, then  批次 norm modules 将 有 FSDP applied 到 them separately 使用 mixed precision disabled. See  _module_classes_to_ignore argument.

MixedPrecision 有 cast_root_forward_inputs=True 和 cast_forward_inputs=False 由 default. 用于  root FSDP instance, its cast_root_forward_inputs takes precedence over its cast_forward_inputs. 用于 non-root FSDP instances, their cast_root_forward_inputs values 是 ignored.  default setting 是 sufficient 用于  typical case where each FSDP instance 有  same MixedPrecision 配置 和 only needs 到 cast inputs 到  param_dtype at  beginning 的  模型’s 前向传播.

用于 nested FSDP instances 使用 different MixedPrecision configurations, we recommend setting individual cast_forward_inputs values 到 configure casting inputs 或 not before each instance’s forward. 在 such  case, since  casts happen before each FSDP instance’s forward,  parent FSDP instance 应该 有 its non-FSDP submodules run before its FSDP submodules 到 avoid  激活 dtype 正在 changed due 到  different MixedPrecision 配置.

 above shows  working 示例. 在  other hand, if 模型[1] 是 replaced 使用 模型[0], meaning that  submodule using different MixedPrecision ran its forward first, then 模型[1] 将 incorrectly see float16 activations instead 的 bfloat16 ones.

This configures CPU offloading.

offload_params (bool) – This specifies whether 到 offload 参数 到 CPU when not involved 在 计算. If True, then this offloads 梯度 到 CPU as well, meaning that  优化器步骤 runs 在 CPU.

StateDictConfig 是  base class 用于 all state_dict 配置 classes. Users 应该 instantiate  child class (e.g. FullStateDictConfig) 在 order 到 configure settings 用于  corresponding state_dict type supported 由 FSDP.

offload_to_cpu (bool) – If True, then FSDP offloads  状态 dict values 到 CPU, 和 if False, then FSDP keeps them 在 GPU. (Default: False)

FullStateDictConfig 是  config class meant 到 是 used 使用 StateDictType.FULL_STATE_DICT. We recommend enabling both offload_to_cpu=True 和 rank0_only=True when saving full 状态 dicts 到 save GPU 内存 和 CPU 内存, respectively. This config class 是 meant 到 是 used via  state_dict_type() context manager as follows:

rank0_only (bool) – If True, then only rank 0 saves  full 状态 dict, 和 nonzero ranks save  empty dict. If False, then all ranks save  full 状态 dict. (Default: False)

ShardedStateDictConfig 是  config class meant 到 是 used 使用 StateDictType.SHARDED_STATE_DICT.

_use_dtensor (bool) – If True, then FSDP saves  状态 dict values as DTensor, 和 if False, then FSDP saves them as ShardedTensor. (Default: False)

_use_dtensor 是  private field 的 ShardedStateDictConfig 和 it 是 used 由 FSDP 到 determine  type 的 状态 dict values. Users 应该 not manually modify _use_dtensor.

OptimStateDictConfig 是  base class 用于 all optim_state_dict 配置 classes. Users 应该 instantiate  child class (e.g. FullOptimStateDictConfig) 在 order 到 configure settings 用于  corresponding optim_state_dict type supported 由 FSDP.

offload_to_cpu (bool) – If True, then FSDP offloads  状态 dict’s 张量 values 到 CPU, 和 if False, then FSDP keeps them 在  original 设备 (which 是 GPU unless 参数 CPU offloading 是 enabled). (Default: True)

rank0_only (bool) – If True, then only rank 0 saves  full 状态 dict, 和 nonzero ranks save  empty dict. If False, then all ranks save  full 状态 dict. (Default: False)

ShardedOptimStateDictConfig 是  config class meant 到 是 used 使用 StateDictType.SHARDED_STATE_DICT.

_use_dtensor (bool) – If True, then FSDP saves  状态 dict values as DTensor, 和 if False, then FSDP saves them as ShardedTensor. (Default: False)

_use_dtensor 是  private field 的 ShardedOptimStateDictConfig 和 it 是 used 由 FSDP 到 determine  type 的 状态 dict values. Users 应该 not manually modify _use_dtensor.

---

## 分布式 Optimizers#

**URL:** https://pytorch.org/docs/stable/分布式.optim.html

**Contents:**
- 分布式 Optimizers#

Created 在: Mar 01, 2021 | Last Updated 在: Jun 16, 2025

分布式 优化器 是 not currently supported when using CUDA tensors

torch.分布式.optim exposes DistributedOptimizer, which takes  list 的 remote 参数 (RRef) 和 runs  优化器 locally 在  workers where  参数 live.  分布式 优化器 可以 use any 的  local 优化器 Base class 到 apply  梯度 在 each worker.

DistributedOptimizer takes remote references 到 参数 scattered across workers 和 applies  given 优化器 locally 用于 each 参数.

This class uses get_gradients() 在 order 到 retrieve  梯度 用于 specific 参数.

Concurrent calls 到 步骤(), either 从  same 或 different clients, 将 是 serialized 在 each worker – as each worker’s 优化器 可以 only work 在 one set 的 梯度 at  time. However, there 是 no guarantee that  full forward-backward-优化器 序列 将 execute 用于 one client at  time. This means that  梯度 正在 applied 可能 not correspond 到  latest 前向传播 executed 在  given worker. Also, there 是 no guaranteed ordering across workers.

DistributedOptimizer creates  local 优化器 使用 TorchScript enabled 由 default, so that 优化器 updates 是 not blocked 由  Python Global Interpreter Lock (GIL) 在  case 的 multithreaded 训练 (e.g. 分布式 模型 并行). This feature 是 currently enabled 用于 most optimizers. You 可以 also follow  recipe 在 PyTorch tutorials 到 enable TorchScript support 用于 your own custom optimizers.

optimizer_class (optim.优化器) –  class 的 优化器 到 instantiate 在 each worker.

params_rref (list[RRef]) – list 的 RRefs 到 local 或 remote 参数 到 optimize.

args – arguments 到 传播 到  优化器 constructor 在 each worker.

kwargs – arguments 到 传播 到  优化器 constructor 在 each worker.

Performs  single 优化 步骤.

This 将 call torch.optim.优化器.步骤() 在 each worker containing 参数 到 是 optimized, 和 将 block until all workers return.  provided context_id 将 是 used 到 retrieve  corresponding context that contains  梯度 that 应该 是 applied 到  参数.

context_id –  自动求导 context id 用于 which we 应该 run  优化器步骤.

Wraps  arbitrary torch.optim.优化器 和 runs post-local SGD, This 优化器 runs local 优化器 at every 步骤. After  warm-up stage, it averages 参数 periodically after  local 优化器 是 applied.

optim (优化器) –  local 优化器.

averager (ModelAverager) –  模型 averager instance 到 run post-localSGD 算法.

This 是  same as torch.optim.优化器 load_state_dict(), but also restores 模型 averager’s 步骤 value 到  one saved 在  provided state_dict.

If there 是 no "步骤" entry 在 state_dict, it 将 raise  warning 和 initialize  模型 averager’s 步骤 到 0.

This 是  same as torch.optim.优化器 state_dict(), but adds  extra entry 到 record 模型 averager’s 步骤 到  checkpoint 到 ensure reload 做 not cause unnecessary warm up again.

Performs  single 优化 步骤 (参数 update).

Wrap  arbitrary optim.优化器 和 shards its states across ranks 在  group.

 sharing 是 done as described 由 ZeRO.

 local 优化器 instance 在 each rank 是 only responsible 用于 updating approximately 1 / world_size 参数 和 hence only needs 到 keep 1 / world_size 优化器 states. After 参数 是 updated locally, each rank 将 广播 its 参数 到 all other peers 到 keep all 模型 副本 在  same 状态. ZeroRedundancyOptimizer 可以 是 used 在 conjunction 使用 torch.nn.并行.分布式数据并行 到 归约 per-rank peak 内存 consumption.

ZeroRedundancyOptimizer uses  sorted-greedy 算法 到 pack  number 的 参数 at each rank. Each 参数 belongs 到  single rank 和 是 not divided among ranks.  partition 是 arbitrary 和 可能 not match  参数 registration 或 usage order.

params (Iterable) –  Iterable 的 torch.张量 s 或 dict s giving all 参数, which 将 是 sharded across ranks.

optimizer_class (torch.nn.优化器) –  class 的  local 优化器.

process_group (进程组, optional) – torch.分布式 进程组 (default: dist.group.WORLD initialized 由 torch.分布式.init_process_group()).

parameters_as_bucket_view (bool, optional) – if True, 参数 是 packed into 桶 到 speed up 通信, 和 param.数据 fields point 到 桶 views at different offsets; if False, each individual 参数 是 communicated separately, 和 each params.数据 stays intact (default: False).

overlap_with_ddp (bool, optional) – if True, 步骤() 是 overlapped 使用 分布式数据并行 ‘s 梯度 同步; this requires (1) either  functional 优化器 用于  optimizer_class argument 或 one 使用  functional equivalent 和 (2) registering  DDP 通信 钩子 constructed 从 one 的  functions 在 ddp_zero_hook.py; 参数 是 packed into 桶 matching those 在 分布式数据并行, meaning that  parameters_as_bucket_view argument 是 ignored. If False, 步骤() runs disjointly after  反向传播 (per normal). (default: False)

**defaults – any trailing arguments, which 是 forwarded 到  local 优化器.

Currently, ZeroRedundancyOptimizer requires that all 的  passed-在 参数 是  same dense type.

If you 传播 overlap_with_ddp=True, 是 wary 的  following: Given  way that overlapping 分布式数据并行 使用 ZeroRedundancyOptimizer 是 currently implemented,  first two 或 three 训练 iterations 做 not perform 参数 updates 在  优化器步骤, depending 在 if static_graph=False 或 static_graph=True, respectively. This 是 because it needs information about  梯度 bucketing strategy used 由 分布式数据并行, which 是 not finalized until  second 前向传播 if static_graph=False 或 until  third 前向传播 if static_graph=True. 到 adjust 用于 this, one option 是 到 prepend dummy inputs.

ZeroRedundancyOptimizer 是 experimental 和 subject 到 change.

Add  参数 group 到  优化器 ‘s param_groups.

This 可以 是 useful when fine tuning  pre-trained 网络, as frozen 层 可以 是 made trainable 和 added 到  优化器 as 训练 progresses.

param_group (dict) – specifies  参数 到 是 optimized 和 group-specific 优化 options.

This method handles updating  shards 在 all partitions but needs 到 是 called 在 all ranks. Calling this 在  subset 的  ranks 将 cause  训练 到 hang because 通信 primitives 是 called depending 在  managed 参数 和 expect all  ranks 到 participate 在  same set 的 参数.

Consolidate  list 的 state_dict s (one per rank) 在  target rank.

到 (int) –  rank that receives  优化器 states (default: 0).

RuntimeError – if overlap_with_ddp=True 和 this method 是 called before this ZeroRedundancyOptimizer instance 有 是 fully initialized, which happens once 分布式数据并行 梯度 桶 有 是 rebuilt.

This needs 到 是 called 在 all ranks.

Return default 设备.

Return  ZeRO join 钩子.

It enables 训练 在 uneven inputs 由 shadowing  collective communications 在  优化器步骤.

梯度 必须 是 properly set before this 钩子 是 called.

kwargs (dict) –  dict containing any keyword arguments 到 modify  behavior 的  join 钩子 at run time; all Joinable instances sharing  same join context manager 是 forwarded  same value 用于 kwargs.

This 钩子 做 not support any keyword arguments; i.e. kwargs 是 unused.

Return 进程 group.

Load  状态 pertaining 到  given rank 从  输入 state_dict, updating  local 优化器 as needed.

state_dict (dict) – 优化器 状态; 应该 是  object returned 从  call 到 state_dict().

RuntimeError – if overlap_with_ddp=True 和 this method 是 called before this ZeroRedundancyOptimizer instance 有 是 fully initialized, which happens once 分布式数据并行 梯度 桶 有 是 rebuilt.

Return  last global 优化器 状态 known 到 this rank.

RuntimeError – if overlap_with_ddp=True 和 this method 是 called before this ZeroRedundancyOptimizer instance 有 是 fully initialized, which happens once 分布式数据并行 梯度 桶 有 是 rebuilt; 或 if this method 是 called without  preceding call 到 consolidate_state_dict().

Perform  single 优化器步骤 和 syncs 参数 across all ranks.

closure (Callable) –  closure that re-evaluates  模型 和 returns  损失; optional 用于 most optimizers.

Optional 损失 depending 在  underlying local 优化器.

Any extra 参数 是 passed 到  base 优化器 as-是.

---

## Torch 分布式 Elastic#

**URL:** https://pytorch.org/docs/stable/分布式.elastic.html

**Contents:**
- Torch 分布式 Elastic#
- Get Started#
- Documentation#

Created 在: Jun 16, 2025 | Last Updated 在: Jul 25, 2025

Makes 分布式 PyTorch fault-tolerant 和 elastic.

---

## Pipeline Parallelism#

**URL:** https://pytorch.org/docs/stable/分布式.pipelining.html

**Contents:**
- Pipeline Parallelism#
- Why Pipeline 并行?#
- What 是 torch.分布式.pipelining?#
- 步骤 1: build PipelineStage#
- 步骤 2: use PipelineSchedule 用于 execution#
- Options 用于 Splitting  模型#
  - Option 1: splitting  模型 manually#
  - Option 2: splitting  模型 automatically#
- Hugging Face Examples#
- Technical Deep Dive#

Created 在: Jun 16, 2025 | Last Updated 在: Aug 13, 2025

torch.分布式.pipelining 是 currently 在 alpha 状态 和 under development. API changes 可能 是 possible. It 是 migrated 从  PiPPy project.

Pipeline Parallelism 是 one 的  primitive parallelism 用于 深度学习. It allows  execution 的  模型 到 是 partitioned such that multiple micro-batches 可以 execute different parts 的  模型 code concurrently. Pipeline parallelism 可以 是  effective technique 用于:

bandwidth-limited clusters

large 模型 推理

 above scenarios share  commonality that  计算 per 设备 cannot hide  通信 的 conventional parallelism, 用于 示例,  权重 all-gather 的 FSDP.

While promising 用于 scaling, pipelining 是 often difficult 到 implement because it needs 到 partition  execution 的  模型 在 addition 到 模型 weights.  partitioning 的 execution often requires intrusive code changes 到 your 模型. Another aspect 的 complexity comes 从 scheduling micro-batches 在  分布式 environment, 使用 数据 flow dependency considered.

 pipelining package provides  toolkit that 做 said things automatically which allows easy 实现 的 pipeline parallelism 在 general 模型.

It consists 的 two parts:  splitting frontend 和  分布式 runtime.  splitting frontend takes your 模型 code as-是, splits it up into “模型 partitions”, 和 captures  数据-flow relationship.  分布式 runtime executes  pipeline stages 在 different 设备 在 并行, handling things like micro-批次 splitting, scheduling, 通信, 和 梯度 propagation, etc.

Overall,  pipelining package provides  following features:

Splitting 的 模型 code based 在 simple specification.

Rich support 用于 pipeline schedules, including GPipe, 1F1B, Interleaved 1F1B 和 Looped BFS, 和 providing  infrastructure 用于 writing customized schedules.

First-class support 用于 cross-host pipeline parallelism, as this 是 where PP 是 typically used (over slower interconnects).

Composability 使用 other PyTorch 并行 techniques such as 数据 并行 (DDP, FSDP) 或 张量 并行.  TorchTitan project demonstrates  “3D 并行” application 在  Llama 模型.

Before we 可以 use  PipelineSchedule, we need 到 create PipelineStage objects that wrap  part 的  模型 running 在 that stage.  PipelineStage 是 responsible 用于 allocating 通信 buffers 和 creating send/recv ops 到 communicate 使用 its peers. It manages intermediate buffers e.g. 用于  outputs 的 forward that 有 not 是 consumed yet, 和 it provides  utility 用于 running  backwards 用于  stage 模型.

 PipelineStage needs 到 know  输入 和 输出 shapes 用于  stage 模型, so that it 可以 correctly allocate 通信 buffers.  shapes 必须 是 static, e.g. at runtime  shapes 可以 not change 从 步骤 到 步骤.  class PipeliningShapeError 将 是 raised if runtime shapes 做 not match  expected shapes. When composing 使用 other paralleisms 或 applying mixed precision, these techniques 必须 是 taken into account so  PipelineStage knows  correct shape (和 dtype) 用于  输出 的  stage 模块 at runtime.

Users 可能 construct  PipelineStage instance directly, 由 passing 在  nn.模块 representing  portion 的  模型 that 应该 run 在  stage. This 可能 require changes 到  original 模型 code. See  示例 在 Option 1: splitting  模型 manually.

Alternatively,  splitting frontend 可以 use 图 partitioning 到 split your 模型 into  series 的 nn.模块 automatically. This technique requires  模型 是 traceable 使用 torch.Export. Composability 的  resulting nn.模块 使用 other parallelism techniques 是 experimental, 和 可能 require some workarounds. Usage 的 this frontend 可能 是 more appealing if  user cannot easily change  模型 code. See Option 2: splitting  模型 automatically 用于 more information.

We 可以 now attach  PipelineStage 到  pipeline schedule, 和 run  schedule 使用 输入 数据. Here 是  GPipe 示例:

Note that  above code needs 到 是 launched 用于 each worker, thus we use  launcher service 到 launch multiple 进程:

到 directly construct  PipelineStage,  user 是 responsible 用于 providing  single nn.模块 instance that owns  relevant nn.参数 和 nn.Buffers, 和 defines  forward() method that executes  操作 relevant 用于 that stage. 用于 示例,  condensed version 的  变换器 class defined 在 Torchtitan shows  pattern 的 building  easily partitionable 模型.

 模型 defined 在 this manner 可以 是 easily configured per stage 由 first initializing  whole 模型 (using meta-设备 到 avoid OOM errors), deleting undesired 层 用于 that stage, 和 then creating  PipelineStage that wraps  模型. 用于 示例:

When composing 使用 other 数据 或 模型 parallelism techniques, output_args 可能 also 是 required, if  输出 shape/dtype 的  模型 chunk 将 是 affected.

If you 有  full 模型 和 做 not want 到 spend time 在 modifying it into  序列 的 “模型 partitions”,  pipeline API 是 here 到 help. Here 是  brief 示例:

If we print  模型, we 可以 see multiple hierarchies, which makes it hard 到 split 由 hand:

Let us see how  pipeline API works:

 pipeline API splits your 模型 given  split_spec, where SplitPoint.BEGINNING stands 用于 adding  split point before execution 的 certain submodule 在  forward 函数, 和 similarly, SplitPoint.END 用于 split point after such.

If we print(pipe), we 可以 see:

 “模型 partitions” 是 represented 由 submodules (submod_0, submod_1), each 的 which 是 reconstructed 使用 original 模型 操作, weights 和 hierarchies. 在 addition,  “root-level” forward 函数 是 reconstructed 到 capture  数据 flow between those partitions. Such 数据 flow 将 是 replayed 由  pipeline runtime later, 在  分布式 fashion.

 Pipe object provides  method 用于 retrieving  “模型 partitions”:

 returned stage_mod 是  nn.模块, 使用 which you 可以 create  优化器, save 或 load checkpoints, 或 apply other parallelisms.

Pipe also allows you 到 create  分布式 stage runtime 在  设备 given  进程组:

Alternatively, if you 将 like 到 build  stage runtime later after some modification 到  stage_mod, you 可以 use  functional version 的  build_stage API. 用于 示例:

 pipeline frontend uses  tracer (torch.export) 到 capture your 模型 into  single 图. If your 模型 是 not full-图’able, you 可以 use our manual frontend below.

在  PiPPy repo where this package 是 original created, we kept examples based 在 unmodified Hugging Face 模型. See  examples/huggingface directory.

First,  pipeline API turns our 模型 into  directed acyclic 图 (DAG) 由 tracing  模型. It traces  模型 using torch.export –  PyTorch 2 full-图 capturing tool.

Then, it groups together  操作 和 参数 needed 由  stage into  reconstructed submodule: submod_0, submod_1, …

Different 从 conventional submodule access methods like 模块.children(),  pipeline API 做 not only cut  模块 structure 的 your 模型, but also  forward 函数 的 your 模型.

This 是 necessary because 模型 structure like 模块.children() merely captures information during 模块.__init__(), 和 做 not capture any information about 模块.forward(). Said differently, 模块.children() lacks information about  following aspects key 到 pipelininig:

Execution order 的 child modules 在 forward

激活 flows between child modules

Whether there 是 any functional operators between child modules (用于 示例, relu 或 add 操作 将 not 是 captured 由 模块.children()).

 pipeline API, 在  contrary, makes sure that  forward behavior 是 truly preserved. It also captures  激活 flow between  partitions, helping  分布式 runtime 到 make correct send/receive calls without human intervention.

Another flexibility 的  pipeline API 是 that split points 可以 是 at arbitrary levels within your 模型 hierarchy. 在  split partitions,  original 模型 hierarchy related 到 that partition 将 是 reconstructed at no cost 到 you. At  result, fully-qualified names (FQNs) pointing 到  submodule 或 参数 将 是 still valid, 和 services that relies 在 FQNs (such as FSDP, TP 或 checkpointing) 可以 still run 使用 your partitioned modules 使用 almost zero code change.

You 可以 implement your own pipeline schedule 由 extending one 的  following two class:

PipelineScheduleSingle

PipelineScheduleMulti

PipelineScheduleSingle 是 用于 schedules that assigns only one stage per rank. PipelineScheduleMulti 是 用于 schedules that assigns multiple stages per rank.

用于 示例, ScheduleGPipe 和 Schedule1F1B 是 subclasses 的 PipelineScheduleSingle. Whereas, ScheduleInterleaved1F1B, ScheduleLoopedBFS, ScheduleInterleavedZeroBubble, 和 ScheduleZBVZeroBubble 是 subclasses 的 PipelineScheduleMulti.

You 可以 turn 在 additional logging using  TORCH_LOGS environment variable 从 torch._logging:

TORCH_LOGS=+pp 将 display logging.DEBUG messages 和 all levels above it.

TORCH_LOGS=pp 将 display logging.INFO messages 和 above.

TORCH_LOGS=-pp 将 display logging.WARNING messages 和 above.

 following set 的 APIs transform your 模型 into  pipeline representation.

Enum representing  points at which  split 可以 occur 在  execution 的  submodule. :ivar BEGINNING: Represents adding  split point before  execution 的  certain submodule 在  forward 函数. :ivar END: Represents adding  split point after  execution 的  certain submodule 在  forward 函数.

Split  模块 based 在  specification.

See Pipe 用于 more details.

模块 (模块) –  模块 到 是 split.

mb_args (tuple[Any, ...]) – 示例 positional inputs, 在 micro-批次 form.

mb_kwargs (Optional[dict[str, Any]]) – 示例 keyword inputs, 在 micro-批次 form. (default: None)

split_spec (Optional[dict[str, torch.分布式.pipelining._IR.SplitPoint]]) –  dictionary using submodule names as split marker. (default: None)

split_policy (Optional[Callable[[GraphModule], GraphModule]]) –  policy 到 use 用于 splitting  模块. (default: None)

 pipeline representation 的 class Pipe.

pipe_split 是  special operator that 是 used 到 mark  boundary between stages 在  模块. It 是 used 到 split  模块 into stages. It 是  no-op if your annotated 模块 是 run eagerly.

 above 示例 将 是 split into two stages.

Class used 到 specify chunking 的 inputs

Given  序列 的 args 和 kwargs, split them into  number 的 chunks according 到 their respective chunking specs.

args (tuple[Any, ...]) – Tuple 的 args

kwargs (Optional[dict[str, Any]]) – Dict 的 kwargs

chunks (int) – Number 的 chunks 到 split  args 和 kwargs into

args_chunk_spec (Optional[tuple[torch.分布式.pipelining.microbatch.TensorChunkSpec, ...]]) – chunking specs 用于 args, 在 same shape as args

kwargs_chunk_spec (Optional[dict[str, torch.分布式.pipelining.microbatch.TensorChunkSpec]]) – chunking specs 用于 kwargs, 在 same shape as kwargs

List 的 sharded args kwargs_split: List 的 sharded kwargs

Given  list 的 chunks, merge them into  single value according 到  chunk spec.

chunks (list[Any]) – list 的 chunks

chunk_spec – Chunking spec 用于  chunks

 class representing  pipeline stage 在  pipeline parallelism setup.

PipelineStage assumes sequential partitioning 的  模型, i.e.  模型 是 split into chunks where outputs 从 one chunk feed into inputs 的  next chunk, 使用 no skip connections.

PipelineStage performs runtime shape/dtype 推理 automatically 由 propagating  outputs 从 stage0 到 stage1 和 so forth, 在 linear order. 到 bypass shape 推理, 传播  input_args 和 output_args 到 each PipelineStage instance.

submodule (nn.模块) –  PyTorch 模块 wrapped 由 this stage.

stage_index (int) –  ID 的 this stage.

num_stages (int) –  total number 的 stages.

设备 (torch.设备) –  设备 where this stage 是 located.

input_args (Union[torch.张量, Tuple[torch.张量]], optional) –  输入 arguments 用于  submodule.

output_args (Union[torch.张量, Tuple[torch.张量]], optional) –  输出 arguments 用于  submodule.

group (dist.进程组, optional) –  进程 group 用于 分布式 训练. If None, default group.

dw_builder (Optional[Callable[[], Callable[..., None]]) – If provided, dw_builder 将 build  new dw_runner 函数 that 将  W action (输入 weights) 用于 F, I, W (Fwd, 输入, 权重) zero bubble schedules.

Create  pipeline stage given  stage_module 到 是 wrapped 由 this stage 和 pipeline information.

stage_module (torch.nn.模块) –  模块 到 是 wrapped 由 this stage

stage_index (int) –  index 的 this stage 在  pipeline

pipe_info (PipeInfo) – information about  pipeline, 可以 是 retrieved 由 pipe.info()

设备 (torch.设备) –  设备 到 是 used 由 this stage

group (Optional[dist.进程组]) –  进程 group 到 是 used 由 this stage

 pipeline stage that 可以 run 使用 PipelineSchedules.

 GPipe schedule. 将 go through all  microbatches 在  fill-drain manner.

 1F1B schedule. 将 perform one forward 和 one backward 在  microbatches 在 steady 状态.

 Interleaved 1F1B schedule. See https://arxiv.org/pdf/2104.04473 用于 details. 将 perform one forward 和 one backward 在  microbatches 在 steady 状态 和 supports multiple stages per rank. When microbatches 是 ready 用于 multiple local stages, Interleaved 1F1B prioritizes  earlier microbatch (also called “depth first”).

This schedule 是 mostly similar 到  original paper. It differs 由 正在 relaxing  requirement 的 num_microbatch % pp_size == 0. Using  flex_pp schedule, we 将 有 num_rounds = max(1, n_microbatches // pp_group_size) 和 it works as long as n_microbatches % num_rounds 是 0. As  few examples, support

pp_group_size = 4, n_microbatches = 10. We 将 有 num_rounds = 2 和 n_microbatches % 2 是 0.

pp_group_size = 4, n_microbatches = 3. We 将 有 num_rounds = 1 和 n_microbatches % 1 是 0.

Breadth-First Pipeline Parallelism. See https://arxiv.org/abs/2211.05953 用于 details. Similar 到 Interleaved 1F1B, Looped BFS supports multiple stages per rank. What 是 different 是 that when microbatches 是 ready 用于 multiple local stages, Loops BFS 将 prioritizes  earlier stage, running all available microbatches at once.

 Interleaved Zero Bubble schedule. See https://arxiv.org/pdf/2401.10241 用于 details. 将 perform one forward 和 one backward 在 inputs 用于  microbatches 在 steady 状态 和 supports multiple stages per rank. Uses  backward 用于 weights 到 fill 在  pipeline bubble.

在 particular this 是 implementing  ZB1P schedule 在  paper.

 Zero Bubble schedule (ZBV variant). See https://arxiv.org/pdf/2401.10241 Section 6 用于 details.

This schedules requires exactly two stages per rank.

This schedule 将 perform one forward 和 one backward 在 inputs 用于  microbatches 在 steady 状态 和 supports multiple stages per rank. Uses backward 使用 respect 到 weights 到 fill 在  pipeline bubble.

This ZB-V schedule 将 有  “zero bubble” property only if time forward == time backward 输入 == time backward weights. 在 practice, this 是 not likely true 用于 real 模型 so alternatively  greedy scheduler 可以 是 implemented 用于 unequal/unbalanced time.

 DualPipeV schedule.  more efficient schedule variant based 在  DualPipe schedule introduced 由 DeepSeek 在 https://arxiv.org/pdf/2412.19437

Based 在  open sourced code 从 deepseek-ai/DualPipe

Base class 用于 single-stage schedules. Implements  步骤 method. Derived classes 应该 implement _step_microbatches.

梯度 是 scaled 由 num_microbatches depending 在  scale_grads argument, defaulting 到 True. This setting 应该 match  配置 的 your loss_fn, which 可能 either average losses (scale_grads=True) 或 sum losses (scale_grads=False).

Run one 迭代 的  pipeline schedule 使用 whole-批次 输入. 将 chunk  输入 into microbatches automatically, 和 go through  microbatches according 到  schedule 实现.

args: positional arguments 到  模型 (as 在 non-pipeline case). kwargs: keyword arguments 到  模型 (as 在 non-pipeline case). target: target 用于  损失 函数. losses:  list 到 store  losses 用于 each microbatch.

Base class 用于 multi-stage schedules. Implements  步骤 method.

梯度 是 scaled 由 num_microbatches depending 在  scale_grads argument, defaulting 到 True. This setting 应该 match  配置 的 your loss_fn, which 可能 either average losses (scale_grads=True) 或 sum losses (scale_grads=False).

Run one 迭代 的  pipeline schedule 使用 whole-批次 输入. 将 chunk  输入 into microbatches automatically, 和 go through  microbatches according 到  schedule 实现.

args: positional arguments 到  模型 (as 在 non-pipeline case). kwargs: keyword arguments 到  模型 (as 在 non-pipeline case). target: target 用于  损失 函数. losses:  list 到 store  losses 用于 each microbatch.

---

## 张量 Parallelism - torch.分布式.张量.并行#

**URL:** https://pytorch.org/docs/stable/分布式.张量.并行.html

**Contents:**
- 张量 Parallelism - torch.分布式.张量.并行#

Created 在: Jun 13, 2025 | Last Updated 在: Jun 13, 2025

张量 Parallelism(TP) 是 built 在 top 的  PyTorch DistributedTensor (DTensor)[https://github.com/pytorch/pytorch/blob/main/torch/分布式/张量/README.md] 和 provides different parallelism styles: Colwise, Rowwise, 和 序列 Parallelism.

张量 Parallelism APIs 是 experimental 和 subject 到 change.

 entrypoint 到 parallelize your nn.模块 using 张量 Parallelism 是:

Apply 张量 Parallelism 在 PyTorch 由 parallelizing modules 或 sub-modules based 在  user-specified plan.

We parallelize 模块 或 sub_modules based 在  parallelize_plan.  parallelize_plan contains ParallelStyle, which indicates how user wants  模块 或 sub_module 到 是 parallelized.

User 可以 also specify different 并行 style per 模块 fully qualified name (FQN).

Note that parallelize_module only accepts  1-D DeviceMesh, if you 有  2-D 或 N-D DeviceMesh, slice  DeviceMesh 到  1-D sub DeviceMesh first then 传播 到 this API(i.e. device_mesh["tp"])

模块 (nn.模块) – 模块 到 是 parallelized.

device_mesh (DeviceMesh, optional) – Object which describes  mesh topology 的 设备 用于  DTensor. If not specified,  call 必须 是 under  DeviceMesh context.

parallelize_plan (Union[ParallelStyle, Dict[str, ParallelStyle]], optional) –  plan used 到 parallelize  模块. It 可以 是 either  ParallelStyle object which contains how we prepare 输入/输出 用于 张量 Parallelism 或 it 可以 是  dict 的 模块 FQN 和 its corresponding ParallelStyle object. If not specified,  call 将 做 nothing at  moment.

src_data_rank (int, optional) –  rank 的  source 数据 用于  logical/global 张量, it 是 used 由 distribute_tensor() 到 scatter/广播  shards/副本 到 other ranks. 由 default, we use group_rank=0 在 each DeviceMesh dimension as  source 数据 到 preserve  single-设备 semantic. If passing None explicitly, parallelize_module() simply uses its local 数据 instead 的 trying 到 preserve  single-设备 semantic via scatter/广播. Default: 0

 nn.模块 object parallelized.

用于 complex 模块 architecture like 注意力, MLP 层, we recommend composing different ParallelStyles together (i.e. ColwiseParallel 和 RowwiseParallel) 和 传播 as  parallelize_plan, 到 achieves  desired sharding 计算.

张量 Parallelism supports  following 并行 styles:

Partition  compatible nn.模块 在  column-wise fashion. Currently supports nn.Linear 和 nn.嵌入. Users 可以 compose it together 使用 RowwiseParallel 到 achieve  sharding 的 more complicated modules. (i.e. MLP, 注意力)

input_layouts (Placement, optional) –  DTensor layout 的 输入 张量 用于  nn.模块, this 是 used 到 annotate  输入 张量 到 become  DTensor. If not specified, we assume  输入 张量 到 是 replicated.

output_layouts (Placement, optional) –  DTensor layout 的  输出 用于  nn.模块, this 是 used 到 ensure  输出 的  nn.模块 使用  user desired layout. If not specified,  输出 张量 是 sharded 在  last dimension.

use_local_output (bool, optional) – Whether 到 use local torch.张量 instead 的 DTensor 用于  模块 输出, default: True.

 ParallelStyle object that represents Colwise sharding 的  nn.模块.

由 default ColwiseParallel 输出 是 sharded 在  last dimension if  output_layouts not specified, if there’re operators that require specific 张量 shape (i.e. before  paired RowwiseParallel), keep 在 mind that if  输出 是 sharded  operator 可能 need 到 是 adjusted 到  sharded size.

Partition  compatible nn.模块 在  row-wise fashion. Currently supports nn.Linear 和 nn.嵌入. Users 可以 compose it 使用 ColwiseParallel 到 achieve  sharding 的 more complicated modules. (i.e. MLP, 注意力)

input_layouts (Placement, optional) –  DTensor layout 的 输入 张量 用于  nn.模块, this 是 used 到 annotate  输入 张量 到 become  DTensor. If not specified, we assume  输入 张量 到 是 sharded 在  last dimension.

output_layouts (Placement, optional) –  DTensor layout 的  输出 用于  nn.模块, this 是 used 到 ensure  输出 的  nn.模块 使用  user desired layout. If not specified,  输出 张量 是 replicated.

use_local_output (bool, optional) – Whether 到 use local torch.张量 instead 的 DTensor 用于  模块 输出, default: True.

 ParallelStyle object that represents Rowwise sharding 的  nn.模块.

SequenceParallel replicates  compatible nn.模块 参数 和 runs  sharded 计算 使用 输入 sharded 在  序列 dimension. This currently supports nn.LayerNorm, nn.丢弃, 和  RMSNorm python 实现

This style implements  操作 that 是 described 在  paper Reducing 激活 Recomputation 在 Large 变换器 模型

If  输入 passed 在 到 this nn.模块 是  torch.张量, it assumes that  输入 是 already sharded 在  序列 dimension 和 converts  输入 到  DTensor sharded 在  序列 dimension. If  输入 passed 在 到 this nn.模块 是 already  DTensor but 是 not sharded 在  序列 dimension, it 将 redistribute  输入 到 是 sharded 在  序列 dimension.

 输出 的  nn.模块 将 是 sharded 在  序列 dimension.

sequence_dim (int, optional) –  序列 dimension 的  输入 张量 用于  nn.模块, this 是 used 到 annotate  输入 张量 到 become  DTensor that 是 sharded 在  序列 dimension, default: 1.

use_local_output (bool, optional) – Whether 到 use local torch.张量 instead 的 DTensor 用于  模块 输出, default: False.

 ParallelStyle object that represents 序列 并行 的  nn.模块.

SequenceParallel style assumes ones 初始化 if there 是 weights 在  nn.模块 (i.e. nn.LayerNorm 或 RMSNorm, 和 they 由 default 有 ones 初始化). If you 有 custom inits 用于  weights 在 those modules, you need 到 广播  weights before/after parallelizing 到 ensure that they 是 replicated.

到 simply configure  nn.模块’s inputs 和 outputs 使用 DTensor layouts 和 perform necessary layout redistributions, without distribute  模块 参数 到 DTensors,  following ParallelStyle s 可以 是 used 在  parallelize_plan when calling parallelize_module:

Configure  nn.模块’s inputs 到 convert  输入 tensors 的  nn.模块 到 DTensors at runtime according 到 input_layouts, 和 perform layout redistribution according 到  desired_input_layouts.

input_layouts (Union[Placement, Tuple[Optional[Placement]]]) –  DTensor layouts 的 输入 tensors 用于  nn.模块, this 是 used 到 convert  输入 tensors 到 DTensors. If some inputs 是 not torch.张量 或 no need 到 convert 到 DTensors, None need 到 是 specified as  placeholder. default: None.

desired_input_layouts (Union[Placement, Tuple[Optional[Placement]]]) –  desired DTensor layout 的 输入 tensors 用于  nn.模块, this 是 used 到 ensure  inputs 的  nn.模块 有  desired DTensor layouts. This argument needs 到 有  same length 使用 input_layouts. default: None.

input_kwarg_layouts (Dict[str, Placement]) –  DTensor layouts 的 输入 kwargs 用于  nn.模块, this 是 used 到 convert  输入 kwarg tensors 到 DTensors. default: None

desired_input_kwarg_layouts – (Dict[str, Placement]):  desired DTensor layout 的 输入 kwargs 用于  nn.模块, this 是 used 到 ensure  inputs 的  nn.模块 有  desired DTensor layouts. default: None.

use_local_output (bool, optional) – Whether 到 use local torch.张量 instead 的 DTensor 用于  模块 inputs, default: False.

 ParallelStyle object that prepares  sharding layouts 的  nn.模块’s inputs.

Configure  nn.模块’s outputs 到 convert  输出 tensors 的  nn.模块 到 DTensors at runtime according 到 output_layouts, 和 perform layout redistribution according 到  desired_output_layouts.

output_layouts (Union[Placement, Tuple[Placement]]) –  DTensor layouts 的 输出 tensors 用于  nn.模块, this 是 used 到 convert  输出 tensors 到 DTensors if they 是 torch.张量. If some outputs 是 not torch.张量 或 no need 到 convert 到 DTensors, None need 到 是 specified as  placeholder.

desired_output_layouts (Union[Placement, Tuple[Placement]]) –  desired DTensor layouts 的 输出 tensors 用于  nn.模块, this 是 used 到 ensure  outputs 的  nn.模块 有  desired DTensor layouts.

use_local_output (bool, optional) – Whether 到 use local torch.张量 instead 的 DTensor 用于  模块 outputs, default: True.

 ParallelStyle object that prepares  sharding layouts 的  nn.模块’s outputs.

Configure  nn.模块’s inputs (和 outputs) 到 convert  输入 tensors (和 输出 tensors, respectively) 的  nn.模块 到 DTensors at runtime according 到 input_layouts (和 output_layouts, respectively), 和 perform layout redistribution according 到  desired_input_layouts (和 desired_output_layouts, respectively). This 是  combination 的 PrepareModuleInput 和 PrepareModuleOutput.

input_layouts (Union[Placement, Tuple[Optional[Placement]]]) –  DTensor layouts 的 输入 tensors 用于  nn.模块, this 是 used 到 convert  输入 tensors 到 DTensors. If some inputs 是 not torch.张量 或 no need 到 convert 到 DTensors, None need 到 是 specified as  placeholder. default: None.

desired_input_layouts (Union[Placement, Tuple[Optional[Placement]]]) –  desired DTensor layout 的 输入 tensors 用于  nn.模块, this 是 used 到 ensure  inputs 的  nn.模块 有  desired DTensor layouts. This argument needs 到 有  same length 使用 input_layouts. default: None.

input_kwarg_layouts (Dict[str, Placement]) –  DTensor layouts 的 输入 kwargs 用于  nn.模块, this 是 used 到 convert  输入 kwarg tensors 到 DTensors. default: None

desired_input_kwarg_layouts – (Dict[str, Placement]):  desired DTensor layout 的 输入 kwargs 用于  nn.模块, this 是 used 到 ensure  inputs 的  nn.模块 有  desired DTensor layouts. default: None.

use_local_input (bool, optional) – Whether 到 use local torch.张量 instead 的 DTensor 用于  模块 inputs, default: False.

output_layouts (Union[Placement, Tuple[Placement]]) –  DTensor layouts 的 输出 tensors 用于  nn.模块, this 是 used 到 convert  输出 tensors 到 DTensors if they 是 torch.张量. If some outputs 是 not torch.张量 或 no need 到 convert 到 DTensors, None need 到 是 specified as  placeholder.

desired_output_layouts (Union[Placement, Tuple[Placement]]) –  desired DTensor layouts 的 输出 tensors 用于  nn.模块, this 是 used 到 ensure  outputs 的  nn.模块 有  desired DTensor layouts.

use_local_output (bool, optional) – Whether 到 use local torch.张量 instead 的 DTensor 用于  模块 outputs, default: True.

 ParallelStyle object that prepares  sharding layouts 的  nn.模块’s inputs 和 outputs.

when using  Shard(dim) as  输入/输出 layouts 用于  above ParallelStyle s, we assume  输入/输出 激活 tensors 是 evenly sharded 在  张量 dimension dim 在  DeviceMesh that TP operates 在. 用于 instance, since RowwiseParallel accepts 输入 that 是 sharded 在  last dimension, it assumes  输入 张量 有 already 是 evenly sharded 在  last dimension. 用于  case 的 uneven sharded 激活 tensors, one 可以 传播 在 DTensor directly 到  partitioned modules, 和 use use_local_output=False 到 return DTensor after each ParallelStyle, where DTensor 可以 track  uneven sharding information.

用于 模型 like 变换器, we recommend users 到 use ColwiseParallel 和 RowwiseParallel together 在  parallelize_plan 用于 achieve  desired sharding 用于  entire 模型 (i.e. 注意力 和 MLP).

Parallelized cross-entropy 损失 计算 (损失 parallelism), 是 supported via  following context manager:

 context manager that enables 损失 parallelism, where efficient parallelized 损失 计算 可以 是 performed when  输入 是 sharded 在  class dimension. Currently only  cross-entropy 损失 是 supported.

Within this context manager, one 可以 use cross_entropy() 或 CrossEntropyLoss as usual, 使用  following assumptions 在  输入 参数.  corresponding backward() call, if any, also needs 到 happen under this context manager.

输入 (DTensor) – 输入 logits. Assumed 到 是 sharded 在  class dimension.

target (Union[torch.张量, DTensor]) – 必须 是 ground truth class indices (class probabilities currently not supported). Assumed 到 是 replicated across  DeviceMesh.

权重 (Union[torch.张量, DTensor], optional) – If given, assumed 到 是 replicated across  DeviceMesh.

label_smoothing – Currently not supported.

 replicated DTensor.

 sharded DTensor 是 manually created here 到 showcase  usage. 在 practice, it 是 usually  输出 的  TP 模块.

---
