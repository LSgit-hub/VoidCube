# GGUF 故障排除指南

## 安装问题

### 构建失败

**错误**：`make: *** No targets specified and no makefile found`

**修复**：
```bash
# 确保你在 llama.cpp 目录中
cd llama.cpp
make
```

**错误**：`fatal error: cuda_runtime.h: No such file or directory`

**修复**：
```bash
# 安装 CUDA 工具包
# Ubuntu
sudo apt install nvidia-cuda-toolkit

# 或设置 CUDA 路径
export CUDA_PATH=/usr/local/cuda
export PATH=$CUDA_PATH/bin:$PATH
make GGML_CUDA=1
```

### Python 绑定问题

**错误**：`ERROR: Failed building wheel for llama-cpp-python`

**修复**：
```bash
# 安装构建依赖
pip install cmake scikit-build-core

# 对于 CUDA 支持
CMAKE_ARGS="-DGGML_CUDA=on" pip install llama-cpp-python --force-reinstall --no-cache-dir

# 对于 Metal (macOS)
CMAKE_ARGS="-DGGML_METAL=on" pip install llama-cpp-python --force-reinstall --no-cache-dir
```

**错误**：`ImportError: libcudart.so.XX: cannot open shared object file`

**修复**：
```bash
# 将 CUDA 库添加到路径
export LD_LIBRARY_PATH=/usr/local/cuda/lib64:$LD_LIBRARY_PATH

# 或使用正确的 CUDA 版本重新安装
pip uninstall llama-cpp-python
CUDACXX=/usr/local/cuda/bin/nvcc CMAKE_ARGS="-DGGML_CUDA=on" pip install llama-cpp-python
```

## 转换问题

### 模型不支持

**错误**：`KeyError: 'model.embed_tokens.weight'`

**修复**：
```bash
# 检查模型架构
python -c "from transformers import AutoConfig; print(AutoConfig.from_pretrained('./model').architectures)"

# 使用适当的转换脚本
# 对于大多数模型：
python convert_hf_to_gguf.py ./model --outfile model.gguf

# 对于旧模型，检查是否需要旧版脚本
```

### 词汇表不匹配

**错误**：`RuntimeError: Vocabulary size mismatch`

**修复**：
```python
# 确保分词器与模型匹配
from transformers import AutoTokenizer, AutoModelForCausalLM

tokenizer = AutoTokenizer.from_pretrained("./model")
model = AutoModelForCausalLM.from_pretrained("./model")

print(f"Tokenizer vocab size: {len(tokenizer)}")
print(f"Model vocab size: {model.config.vocab_size}")

# 如果不匹配，在转换前调整嵌入大小
model.resize_token_embeddings(len(tokenizer))
model.save_pretrained("./model-fixed")
```

### 转换期间内存不足

**错误**：转换期间 `torch.cuda.OutOfMemoryError`

**修复**：
```bash
# 使用 CPU 进行转换
CUDA_VISIBLE_DEVICES="" python convert_hf_to_gguf.py ./model --outfile model.gguf

# 或使用低内存模式
python convert_hf_to_gguf.py ./model --outfile model.gguf --outtype f16
```

## 量化问题

### 输出文件大小错误

**问题**：量化文件比预期大

**检查**：
```bash
# 验证量化类型
./llama-cli -m model.gguf --verbose

# 7B 模型的预期大小：
# Q4_K_M: ~4.1 GB
# Q5_K_M: ~4.8 GB
# Q8_0: ~7.2 GB
# F16: ~13.5 GB
```

### 量化崩溃

**错误**：量化期间 `Segmentation fault`

**修复**：
```bash
# 增加栈大小
ulimit -s unlimited

# 或使用更少线程
./llama-quantize -t 4 model-f16.gguf model-q4.gguf Q4_K_M
```

### 量化后质量差

**问题**：量化后模型输出乱码

**解决方案**：

1. **使用重要性矩阵**：
```bash
# 使用良好的校准数据生成 imatrix
./llama-imatrix -m model-f16.gguf \
    -f wiki_sample.txt \
    --chunk 512 \
    -o model.imatrix

# 使用 imatrix 量化
./llama-quantize --imatrix model.imatrix \
    model-f16.gguf model-q4_k_m.gguf Q4_K_M
```

2. **尝试更高精度**：
```bash
# 使用 Q5_K_M 或 Q6_K 而非 Q4
./llama-quantize model-f16.gguf model-q5_k_m.gguf Q5_K_M
```

3. **检查原始模型**：
```bash
# 先测试 FP16 版本
./llama-cli -m model-f16.gguf -p "Hello, how are you?" -n 50
```

## 推理问题

### 生成缓慢

**问题**：生成比预期慢

**解决方案**：

1. **启用 GPU 卸载**：
```bash
./llama-cli -m model.gguf -ngl 35 -p "Hello"
```

2. **优化批大小**：
```python
llm = Llama(
    model_path="model.gguf",
    n_batch=512,        # 增加以加速提示处理
    n_gpu_layers=35
)
```

3. **使用适当的线程**：
```bash
# 匹配物理核心，而非逻辑核心
./llama-cli -m model.gguf -t 8 -p "Hello"
```

4. **启用 Flash Attention**（如果支持）：
```bash
./llama-cli -m model.gguf -ngl 35 --flash-attn -p "Hello"
```

### 内存不足

**错误**：`CUDA out of memory` 或系统冻结

**解决方案**：

1. **减少 GPU 层数**：
```python
# 从低开始并增加
llm = Llama(model_path="model.gguf", n_gpu_layers=10)
```

2. **使用更小的量化**：
```bash
./llama-quantize model-f16.gguf model-q3_k_m.gguf Q3_K_M
```

3. **减少上下文长度**：
```python
llm = Llama(
    model_path="model.gguf",
    n_ctx=2048,  # 从 4096 减少
    n_gpu_layers=35
)
```

4. **量化 KV 缓存**：
```python
llm = Llama(
    model_path="model.gguf",
    type_k=2,    # Q4_0 用于 K 缓存
    type_v=2,    # Q4_0 用于 V 缓存
    n_gpu_layers=35
)
```

### 垃圾输出

**问题**：模型输出随机字符或无意义内容

**诊断**：
```python
# 检查模型加载
llm = Llama(model_path="model.gguf", verbose=True)

# 使用简单提示测试
output = llm("1+1=", max_tokens=5, temperature=0)
print(output)
```

**解决方案**：

1. **检查模型完整性**：
```bash
# 验证 GGUF 文件
./llama-cli -m model.gguf --verbose 2>&1 | head -50
```

2. **使用正确的聊天格式**：
```python
llm = Llama(
    model_path="model.gguf",
    chat_format="llama-3"  # 匹配你的模型：chatml、mistral 等
)
```

3. **检查温度**：
```python
# 使用较低温度获得确定性输出
output = llm("Hello", max_tokens=50, temperature=0.1)
```

### 令牌问题

**错误**：`RuntimeError: unknown token` 或编码错误

**修复**：
```python
# 确保 UTF-8 编码
prompt = "Hello, world!".encode('utf-8').decode('utf-8')
output = llm(prompt, max_tokens=50)
```

## 服务器问题

### 连接被拒绝

**错误**：访问服务器时 `Connection refused`

**修复**：
```bash
# 绑定到所有接口
./llama-server -m model.gguf --host 0.0.0.0 --port 8080

# 检查端口是否被占用
lsof -i :8080
```

### 负载下服务器崩溃

**问题**：多个并发请求时服务器崩溃

**解决方案**：

1. **限制并行度**：
```bash
./llama-server -m model.gguf \
    --parallel 2 \
    -c 4096 \
    --cont-batching
```

2. **添加请求超时**：
```bash
./llama-server -m model.gguf --timeout 300
```

3. **监控内存**：
```bash
watch -n 1 nvidia-smi  # 对于 GPU
watch -n 1 free -h     # 对于 RAM
```

### API 兼容性问题

**问题**：OpenAI 客户端无法与服务器配合工作

**修复**：
```python
from openai import OpenAI

# 使用正确的 base URL 格式
client = OpenAI(
    base_url="http://localhost:8080/v1",  # 包含 /v1
    api_key="not-needed"
)

# 使用正确的模型名称
response = client.chat.completions.create(
    model="local",  # 或实际的模型名称
    messages=[{"role": "user", "content": "Hello"}]
)
```

## Apple Silicon 问题

### Metal 不工作

**问题**：Metal 加速未启用

**检查**：
```bash
# 验证 Metal 支持
./llama-cli -m model.gguf --verbose 2>&1 | grep -i metal
```

**修复**：
```bash
# 使用 Metal 重新构建
make clean
make GGML_METAL=1

# Python 绑定
CMAKE_ARGS="-DGGML_METAL=on" pip install llama-cpp-python --force-reinstall
```

### M1/M2 上内存使用不正确

**问题**：模型使用过多统一内存

**修复**：
```python
# 为 Metal 卸载所有层
llm = Llama(
    model_path="model.gguf",
    n_gpu_layers=99,    # 卸载所有内容
    n_threads=1         # Metal 处理并行
)
```

## 调试

### 启用详细输出

```bash
# CLI 详细模式
./llama-cli -m model.gguf --verbose -p "Hello" -n 50

# Python 详细模式
llm = Llama(model_path="model.gguf", verbose=True)
```

### 检查模型元数据

```bash
# 查看 GGUF 元数据
./llama-cli -m model.gguf --verbose 2>&1 | head -100
```

### 验证 GGUF 文件

```python
import struct

def validate_gguf(filepath):
    with open(filepath, 'rb') as f:
        magic = f.read(4)
        if magic != b'GGUF':
            print(f"Invalid magic: {magic}")
            return False

        version = struct.unpack('<I', f.read(4))[0]
        print(f"GGUF version: {version}")

        tensor_count = struct.unpack('<Q', f.read(8))[0]
        metadata_count = struct.unpack('<Q', f.read(8))[0]
        print(f"Tensors: {tensor_count}, Metadata: {metadata_count}")

        return True

validate_gguf("model.gguf")
```

## 获取帮助

1. **GitHub Issues**：https://github.com/ggml-org/llama.cpp/issues
2. **讨论**：https://github.com/ggml-org/llama.cpp/discussions
3. **Reddit**：r/LocalLLaMA

### 报告问题

包含：
- llama.cpp 版本/提交哈希
- 使用的构建命令
- 模型名称和量化
- 完整错误消息/堆栈跟踪
- 硬件：CPU/GPU 型号、RAM、VRAM
- 操作系统版本
- 最小复现步骤
