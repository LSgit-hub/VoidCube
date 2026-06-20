# GGUF 高级使用指南

## 推测解码

### 草稿模型方法

```bash
# 使用较小模型作为草稿以加速生成
./llama-speculative \
    -m large-model-q4_k_m.gguf \
    -md draft-model-q4_k_m.gguf \
    -p "Write a story about AI" \
    -n 500 \
    --draft 8  # 验证前的草稿令牌数
```

### 自推测解码

```bash
# 使用相同模型但不同上下文进行推测
./llama-cli -m model-q4_k_m.gguf \
    --lookup-cache-static lookup.bin \
    --lookup-cache-dynamic lookup-dynamic.bin \
    -p "Hello world"
```

## 批量推理

### 处理多个提示

```python
from llama_cpp import Llama

llm = Llama(
    model_path="model-q4_k_m.gguf",
    n_ctx=4096,
    n_gpu_layers=35,
    n_batch=512  # 更大批量用于并行处理
)

prompts = [
    "What is Python?",
    "Explain machine learning.",
    "Describe neural networks."
]

# 批量处理（每个提示获得独立上下文）
for prompt in prompts:
    output = llm(prompt, max_tokens=100)
    print(f"Q: {prompt}")
    print(f"A: {output['choices'][0]['text']}\n")
```

### 服务器批处理

```bash
# 启动带批处理的服务器
./llama-server -m model-q4_k_m.gguf \
    --host 0.0.0.0 \
    --port 8080 \
    -ngl 35 \
    -c 4096 \
    --parallel 4        # 并发请求
    --cont-batching     # 连续批处理
```

## 自定义模型转换

### 带词汇表修改的转换

```python
# custom_convert.py
import sys
sys.path.insert(0, './llama.cpp')

from convert_hf_to_gguf import main
from gguf import GGUFWriter

# 带修改词汇表的自定义转换
def convert_with_custom_vocab(model_path, output_path):
    # 加载并修改分词器
    from transformers import AutoTokenizer
    tokenizer = AutoTokenizer.from_pretrained(model_path)

    # 如需要添加特殊令牌
    special_tokens = {"additional_special_tokens": ["<|custom|>"]}
    tokenizer.add_special_tokens(special_tokens)
    tokenizer.save_pretrained(model_path)

    # 然后运行标准转换
    main([model_path, "--outfile", output_path])
```

### 转换特定架构

```bash
# 对于 Mistral 风格模型
python convert_hf_to_gguf.py ./mistral-model \
    --outfile mistral-f16.gguf \
    --outtype f16

# 对于 Qwen 模型
python convert_hf_to_gguf.py ./qwen-model \
    --outfile qwen-f16.gguf \
    --outtype f16

# 对于 Phi 模型
python convert_hf_to_gguf.py ./phi-model \
    --outfile phi-f16.gguf \
    --outtype f16
```

## 高级量化

### 混合量化

```bash
# 对不同层类型使用不同量化
./llama-quantize model-f16.gguf model-mixed.gguf Q4_K_M \
    --allow-requantize \
    --leave-output-tensor
```

### 带令牌嵌入的量化

```bash
# 保持嵌入为更高精度
./llama-quantize model-f16.gguf model-q4.gguf Q4_K_M \
    --token-embedding-type f16
```

### IQ 量化（重要性感知）

```bash
# 带重要性的超低比特量化
./llama-quantize --imatrix model.imatrix \
    model-f16.gguf model-iq2_xxs.gguf IQ2_XXS

# 可用 IQ 类型：IQ2_XXS, IQ2_XS, IQ2_S, IQ3_XXS, IQ3_XS, IQ3_S, IQ4_XS
```

## 内存优化

### 内存映射

```python
from llama_cpp import Llama

# 对大模型使用内存映射
llm = Llama(
    model_path="model-q4_k_m.gguf",
    use_mmap=True,       # 内存映射模型
    use_mlock=False,     # 不锁定在 RAM
    n_gpu_layers=35
)
```

### 部分 GPU 卸载

```python
# 基于 VRAM 计算要卸载的层数
import subprocess

def get_free_vram_gb():
    result = subprocess.run(
        ['nvidia-smi', '--query-gpu=memory.free', '--format=csv,nounits,noheader'],
        capture_output=True, text=True
    )
    return int(result.stdout.strip()) / 1024

# 基于 VRAM 估算层数（粗略：7B Q4 每层 0.5GB）
free_vram = get_free_vram_gb()
layers_to_offload = int(free_vram / 0.5)

llm = Llama(
    model_path="model-q4_k_m.gguf",
    n_gpu_layers=min(layers_to_offload, 35)  # 上限为总层数
)
```

### KV 缓存优化

```python
from llama_cpp import Llama

# 为长上下文优化 KV 缓存
llm = Llama(
    model_path="model-q4_k_m.gguf",
    n_ctx=8192,          # 大上下文
    n_gpu_layers=35,
    type_k=1,            # Q8_0 用于 K 缓存 (1)
    type_v=1,            # Q8_0 用于 V 缓存 (1)
    # 或使用 Q4_0 (2) 获得更多压缩
)
```

## 上下文管理

### 上下文移位

```python
from llama_cpp import Llama

llm = Llama(
    model_path="model-q4_k_m.gguf",
    n_ctx=4096,
    n_gpu_layers=35
)

# 通过上下文移位处理长对话
conversation = []
max_history = 10

def chat(user_message):
    conversation.append({"role": "user", "content": user_message})

    # 仅保留最近历史
    if len(conversation) > max_history * 2:
        conversation = conversation[-max_history * 2:]

    response = llm.create_chat_completion(
        messages=conversation,
        max_tokens=256
    )

    assistant_message = response["choices"][0]["message"]["content"]
    conversation.append({"role": "assistant", "content": assistant_message})
    return assistant_message
```

### 保存和加载状态

```bash
# 保存状态到文件
./llama-cli -m model.gguf \
    -p "Once upon a time" \
    --save-session session.bin \
    -n 100

# 加载并继续
./llama-cli -m model.gguf \
    --load-session session.bin \
    -p " and they lived" \
    -n 100
```

## 语法约束生成

### JSON 输出

```python
from llama_cpp import Llama, LlamaGrammar

# 定义 JSON 语法
json_grammar = LlamaGrammar.from_string('''
root ::= object
object ::= "{" ws pair ("," ws pair)* "}" ws
pair ::= string ":" ws value
value ::= string | number | object | array | "true" | "false" | "null"
array ::= "[" ws value ("," ws value)* "]" ws
string ::= "\\"" [^"\\\\]* "\\""
number ::= [0-9]+
ws ::= [ \\t\\n]*
''')

llm = Llama(model_path="model-q4_k_m.gguf", n_gpu_layers=35)

output = llm(
    "Output a JSON object with name and age:",
    grammar=json_grammar,
    max_tokens=100
)
print(output["choices"][0]["text"])
```

### 自定义语法

```python
# 特定格式的语法
answer_grammar = LlamaGrammar.from_string('''
root ::= "Answer: " letter "\\n" "Explanation: " explanation
letter ::= [A-D]
explanation ::= [a-zA-Z0-9 .,!?]+
''')

output = llm(
    "Q: What is 2+2? A) 3 B) 4 C) 5 D) 6",
    grammar=answer_grammar,
    max_tokens=100
)
```

## LoRA 集成

### 加载 LoRA 适配器

```bash
# 运行时应用 LoRA
./llama-cli -m base-model-q4_k_m.gguf \
    --lora lora-adapter.gguf \
    --lora-scale 1.0 \
    -p "Hello!"
```

### 多个 LoRA 适配器

```bash
# 堆叠多个适配器
./llama-cli -m base-model.gguf \
    --lora adapter1.gguf --lora-scale 0.5 \
    --lora adapter2.gguf --lora-scale 0.5 \
    -p "Hello!"
```

### Python LoRA 使用

```python
from llama_cpp import Llama

llm = Llama(
    model_path="base-model-q4_k_m.gguf",
    lora_path="lora-adapter.gguf",
    lora_scale=1.0,
    n_gpu_layers=35
)
```

## 嵌入生成

### 提取嵌入

```python
from llama_cpp import Llama

llm = Llama(
    model_path="model-q4_k_m.gguf",
    embedding=True,      # 启用嵌入模式
    n_gpu_layers=35
)

# 获取嵌入
embeddings = llm.embed("This is a test sentence.")
print(f"Embedding dimension: {len(embeddings)}")
```

### 批量嵌入

```python
texts = [
    "Machine learning is fascinating.",
    "Deep learning uses neural networks.",
    "Python is a programming language."
]

embeddings = [llm.embed(text) for text in texts]

# 计算相似度
import numpy as np

def cosine_similarity(a, b):
    return np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b))

sim = cosine_similarity(embeddings[0], embeddings[1])
print(f"Similarity: {sim:.4f}")
```

## 性能调优

### 基准测试脚本

```python
import time
from llama_cpp import Llama

def benchmark(model_path, prompt, n_tokens=100, n_runs=5):
    llm = Llama(
        model_path=model_path,
        n_gpu_layers=35,
        n_ctx=2048,
        verbose=False
    )

    # 预热
    llm(prompt, max_tokens=10)

    # 基准测试
    times = []
    for _ in range(n_runs):
        start = time.time()
        output = llm(prompt, max_tokens=n_tokens)
        elapsed = time.time() - start
        times.append(elapsed)

    avg_time = sum(times) / len(times)
    tokens_per_sec = n_tokens / avg_time

    print(f"Model: {model_path}")
    print(f"Avg time: {avg_time:.2f}s")
    print(f"Tokens/sec: {tokens_per_sec:.1f}")

    return tokens_per_sec

# 比较量化
for quant in ["q4_k_m", "q5_k_m", "q8_0"]:
    benchmark(f"model-{quant}.gguf", "Explain quantum computing:", 100)
```

### 最优配置查找器

```python
def find_optimal_config(model_path, target_vram_gb=8):
    """为目标 VRAM 查找最优 n_gpu_layers 和 n_batch。"""
    from llama_cpp import Llama
    import gc

    best_config = None
    best_speed = 0

    for n_gpu_layers in range(0, 50, 5):
        for n_batch in [128, 256, 512, 1024]:
            try:
                gc.collect()
                llm = Llama(
                    model_path=model_path,
                    n_gpu_layers=n_gpu_layers,
                    n_batch=n_batch,
                    n_ctx=2048,
                    verbose=False
                )

                # 快速基准测试
                start = time.time()
                llm("Hello", max_tokens=50)
                speed = 50 / (time.time() - start)

                if speed > best_speed:
                    best_speed = speed
                    best_config = {
                        "n_gpu_layers": n_gpu_layers,
                        "n_batch": n_batch,
                        "speed": speed
                    }

                del llm
                gc.collect()

            except Exception as e:
                print(f"OOM at layers={n_gpu_layers}, batch={n_batch}")
                break

    return best_config
```

## 多 GPU 设置

### 跨 GPU 分配

```bash
# 将模型分割到多个 GPU
./llama-cli -m large-model.gguf \
    --tensor-split 0.5,0.5 \
    -ngl 60 \
    -p "Hello!"
```

### Python 多 GPU

```python
import os
os.environ["CUDA_VISIBLE_DEVICES"] = "0,1"

from llama_cpp import Llama

llm = Llama(
    model_path="large-model-q4_k_m.gguf",
    n_gpu_layers=60,
    tensor_split=[0.5, 0.5]  # 在 2 个 GPU 间均匀分割
)
```

## 自定义构建

### 带所有优化构建

```bash
# 带所有 CPU 优化的干净构建
make clean
LLAMA_OPENBLAS=1 LLAMA_BLAS_VENDOR=OpenBLAS make -j

# 带 CUDA 和 cuBLAS
make clean
GGML_CUDA=1 LLAMA_CUBLAS=1 make -j

# 带特定 CUDA 架构
GGML_CUDA=1 CUDA_DOCKER_ARCH=sm_86 make -j
```

### CMake 构建

```bash
mkdir build && cd build
cmake .. -DGGML_CUDA=ON -DCMAKE_BUILD_TYPE=Release
cmake --build . --config Release -j
```
