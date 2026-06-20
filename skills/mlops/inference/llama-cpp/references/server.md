# 服务器部署指南

llama.cpp 服务器的生产部署，提供 OpenAI 兼容 API。

## 服务器模式

### llama-server

```bash
# 基本服务器
./llama-server \
    -m models/llama-2-7b-chat.Q4_K_M.gguf \
    --host 0.0.0.0 \
    --port 8080 \
    -c 4096  # 上下文大小

# 带 GPU 加速
./llama-server \
    -m models/llama-2-70b.Q4_K_M.gguf \
    -ngl 40  # 卸载 40 层到 GPU
```

## OpenAI 兼容 API

### 聊天补全
```bash
curl http://localhost:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "llama-2",
    "messages": [
      {"role": "system", "content": "You are helpful"},
      {"role": "user", "content": "Hello"}
    ],
    "temperature": 0.7,
    "max_tokens": 100
  }'
```

### 流式输出
```bash
curl http://localhost:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "llama-2",
    "messages": [{"role": "user", "content": "Count to 10"}],
    "stream": true
  }'
```

## Docker 部署

**Dockerfile**：
```dockerfile
FROM ubuntu:22.04
RUN apt-get update && apt-get install -y git build-essential
RUN git clone https://github.com/ggerganov/llama.cpp
WORKDIR /llama.cpp
RUN make LLAMA_CUDA=1
COPY models/ /models/
EXPOSE 8080
CMD ["./llama-server", "-m", "/models/model.gguf", "--host", "0.0.0.0", "--port", "8080"]
```

**运行**：
```bash
docker run --gpus all -p 8080:8080 llama-cpp:latest
```

## 监控

```bash
# 服务器指标端点
curl http://localhost:8080/metrics

# 健康检查
curl http://localhost:8080/health
```

**指标**：
- requests_total
- tokens_generated
- prompt_tokens
- completion_tokens
- kv_cache_tokens

## 负载均衡

**NGINX**：
```nginx
upstream llama_cpp {
    server llama1:8080;
    server llama2:8080;
}

server {
    location / {
        proxy_pass http://llama_cpp;
        proxy_read_timeout 300s;
    }
}
```

## 性能调优

**并行请求**：
```bash
./llama-server \
    -m model.gguf \
    -np 4  # 4 个并行槽位
```

**连续批处理**：
```bash
./llama-server \
    -m model.gguf \
    --cont-batching  # 启用连续批处理
```

**上下文缓存**：
```bash
./llama-server \
    -m model.gguf \
    --cache-prompt  # 缓存已处理的提示
```
