# OBLITERATUS 分析模块 — 参考

OBLITERATUS 包含 28 个分析模块，用于 LLM 拒绝行为的机械可解释性分析。
这些模块帮助理解拒绝行为如何以及在何处被编码，然后再执行 abliteration。

---

## 核心分析（首先运行这些）

### 1. 对齐印记检测（`alignment_imprint.py`）
指纹识别模型是通过 DPO、RLHF、CAI 还是 SFT 训练的。
这决定了哪种提取策略效果最好。

### 2. 概念锥几何（`concept_geometry.py`）
确定拒绝是单一线性方向还是多面锥
（多机制集合）。单一方向模型对 `basic` 响应良好；
多面模型需要 `advanced` 或 `surgical`。

### 3. 拒绝 Logit Lens（`logit_lens.py`）
通过将中间层表示解码到 token 空间，
识别模型"决定"拒绝的具体层。

### 4. 衔尾蛇检测（`anti_ouroboros.py`）
识别模型在切除后是否尝试"自我修复"拒绝行为。
报告风险分数（0-1）。高分意味着需要额外的细化轮次。

### 5. 因果追踪（`causal_tracing.py`）
使用激活补丁识别哪些组件（层、头、MLP）
对拒绝行为是因果必要的。

---

## 几何分析

### 6. 跨层对齐（`cross_layer.py`）
测量拒绝方向在不同层之间的对齐程度。高对齐
意味着拒绝信号一致；低对齐表明存在层特定机制。

### 7. 残差流分解（`residual_stream.py`）
将残差流分解为注意力和 MLP 贡献，
以了解哪种组件类型对拒绝贡献更大。

### 8. 黎曼流形几何（`riemannian_manifold.py`）
分析拒绝方向附近权重流形的曲率和几何。
指导在不破坏流形结构的情况下可以多激进地应用投影。

### 9. 白化 SVD（`whitened_svd.py`）
协方差归一化的 SVD 提取，将护栏信号与
自然激活方差分离。对于高激活方差模型比标准 SVD 更精确。

### 10. 概念锥几何（扩展）
映射拒绝的完整多面结构，包括锥角、
面数和交集模式。

---

## 探测与分类

### 11. 激活探测（`activation_probing.py`）
切除后验证 — 在 abliteration 后探测残留拒绝概念
以确保完全移除。

### 12. 探测分类器（`probing_classifiers.py`）
训练线性分类器在激活中检测拒绝。既用于
之前（验证拒绝存在）也用于之后（验证已移除）。

### 13. 激活补丁（`activation_patching.py`）
交换干预 — 在拒绝和顺从运行之间交换激活
以识别因果组件。

### 14. 调谐 Lens（`tuned_lens.py`）
logit lens 的训练版本，通过学习每层的仿射变换
提供更准确的逐层解码。

### 15. 多 Token 位置分析（`multi_token_position.py`）
分析跨多个 token 位置的拒绝信号，而不仅仅是
最后一个 token。对于将拒绝分布在序列中的模型很重要。

---

## Abliteration 与操作

### 16. 基于 SAE 的 Abliteration（`sae_abliteration.py`）
使用稀疏自编码器特征识别和移除特定拒绝
特征。比基于方向的方法更精细。

### 17. 引导向量（`steering_vectors.py`）
创建并应用推理时引导向量以实现可逆的拒绝
修改。包括 `SteeringVectorFactory` 和 `SteeringHookManager`。

### 18. LEACE 概念擦除（`leace.py`）
通过闭式估计的线性擦除 — 数学上最优的线性
概念移除。既可作为分析模块也可作为方向提取方法。

### 19. 稀疏手术（`sparse_surgery.py`**
高精度权重修改，针对单个神经元和
权重矩阵条目而非完整方向。

### 20. 条件 Abliteration（`conditional_abliteration.py`）
定向移除，仅影响特定拒绝类别同时
保留其他（例如，移除武器拒绝但保留 CSAM 拒绝）。

---

## 迁移与鲁棒性

### 21. 跨模型迁移（`cross_model_transfer.py`）
测试从一个模型提取的拒绝方向是否迁移到
另一个架构。测量护栏方向的普适性。

### 22. 防御鲁棒性（`defense_robustness.py`）
评估 abliteration 对各种防御机制
和重新对齐尝试的鲁棒性。

### 23. 谱认证（`spectral_certification.py`**
使用投影的谱分析提供拒绝移除完整性的
数学界限。

### 24. Wasserstein 最优提取（`wasserstein_optimal.py`）
使用最优传输理论进行更精确的方向提取，
最小化分布偏移。

### 25. Wasserstein 迁移（`wasserstein_transfer.py`）
使用 Wasserstein 距离在模型间进行分布迁移，
用于跨架构拒绝方向映射。

---

## 高级 / 研究

### 26. 贝叶斯核投影（`bayesian_kernel_projection.py`）
概率特征映射，估计拒绝方向识别中的不确定性。

### 27. 跨模型普适性指数
测量护栏方向是否跨不同模型
架构和训练机制泛化。

### 28. 可视化（`visualization.py`）
所有分析模块的绑图和图表工具。生成
热图、方向图和逐层分析图表。

---

## 运行分析

### 通过 CLI
```bash
# 从 YAML 配置运行分析
obliteratus run analysis-study.yaml --preset quick

# 可用的研究预设：
# quick     — 快速健全性检查（2-3 个模块）
# full      — 所有核心 + 几何分析
# jailbreak — 拒绝电路定位
# knowledge — 知识保留分析
# robustness — 压力测试 / 防御评估
```

### 通过 YAML 配置
有关完整示例，请参阅 `templates/analysis-study.yaml` 模板。
使用以下方式加载：`skill_view(name="obliteratus", file_path="templates/analysis-study.yaml")`
