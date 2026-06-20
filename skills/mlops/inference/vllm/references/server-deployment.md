# 服务器部署模式

## 内容
- Docker 部署
- Kubernetes 部署
- 使用 Nginx 负载均衡
- 多节点分布式服务
- 生产配置示例
- 健康检查和监控

## Docker 部署

**基本 Dockerfile**：
```dockerfile
FROM nvidia/cuda:12.1.0-devel-ubuntu22.04

RUN apt-get update && apt-get install -y python3-pip
RUN pip install vllm

EXPOSE 8000

CMD ["vllm", "serve", "meta-llama/Llama-3-8B-Instruct", \
     "--host", "0.0.0.0", "--port", "8000", \
     "--gpu-memory-utilization", "0.9"]
```

**构建并运行**：
```bash
docker build -t vllm-server .
docker run --gpus all -p 8000:8000 vllm-server
```

**Docker Compose**（带指标）：
```yaml
version: '3.8'
services:
  vllm:
    image: vllm/vllm-openai:latest
    command: >
      --model meta-llama/Llama-3-8B-Instruct
      --gpu-memory-utilization 0.9
      --enable-metrics
      --metrics-port 9090
    ports:
      - "8000:8000"
      - "9090:9090"
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: all
              capabilities: [gpu]
```

## Kubernetes 部署

**部署清单**：
```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: vllm-server
spec:
  replicas: 2
  selector:
    matchLabels:
      app: vllm
  template:
    metadata:
      labels:
        app: vllm
    spec:
      containers:
      - name: vllm
        image: vllm/vllm-openai:latest
        args:
          - "--model=meta-llama/Llama-3-8B-Instruct"
          - "--gpu-memory-utilization=0.9"
          - "--enable-prefix-caching"
        resources:
          limits:
            nvidia.com/gpu: 1
        ports:
        - containerPort: 8000
          name: http
        - containerPort: 9090
          name: metrics
        readinessProbe:
          httpGet:
            path: /health
            port: 8000
          initialDelaySeconds: 30
          periodSeconds: 10
        livenessProbe:
          httpGet:
            path: /health
            port: 8000
          initialDelaySeconds: 60
          periodSeconds: 30
---
apiVersion: v1
kind: Service
metadata:
  name: vllm-service
spec:
  selector:
    app: vllm
  ports:
  - port: 8000
    targetPort: 8000
    name: http
  - port: 9090
    targetPort: 9090
    name: metrics
  type: LoadBalancer
```

## 使用 Nginx 负载均衡

**Nginx 配置**：
```nginx
upstream vllm_backend {
    least_conn;  # 路由到负载最低的服务器
    server localhost:8001;
    server localhost:8002;
    server localhost:8003;
}

server {
    listen 80;

    location / {
        proxy_pass http://vllm_backend;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;

        # 长时间推理的超时设置
        proxy_read_timeout 300s;
        proxy_connect_timeout 75s;
    }

    # 指标端点
    location /metrics {
        proxy_pass http://localhost:9090/metrics;
    }
}
```

**启动多个 vLLM 实例**：
```bash
# 终端 1
vllm serve MODEL --port 8001 --tensor-parallel-size 1

# 终端 2
vllm serve MODEL --port 8002 --tensor-parallel-size 1

# 终端 3
vllm serve MODEL --port 8003 --tensor-parallel-size 1

# 启动 Nginx
nginx -c /path/to/nginx.conf
```

## 多节点分布式服务

对于单节点无法容纳的大模型：

**节点 1**（主节点）：
```bash
export MASTER_ADDR=192.168.1.10
export MASTER_PORT=29500
export RANK=0
export WORLD_SIZE=2

vllm serve meta-llama/Llama-2-70b-hf \
  --tensor-parallel-size 8 \
  --pipeline-parallel-size 2
```

**节点 2**（工作节点）：
```bash
export MASTER_ADDR=192.168.1.10
export MASTER_PORT=29500
export RANK=1
export WORLD_SIZE=2

vllm serve meta-llama/Llama-2-70b-hf \
  --tensor-parallel-size 8 \
  --pipeline-parallel-size 2
```

## 生产配置示例

**高吞吐量**（批处理密集型工作负载）：
```bash
vllm serve MODEL \
  --max-num-seqs 512 \
  --gpu-memory-utilization 0.95 \
  --enable-prefix-caching \
  --trust-remote-code
```

**低延迟**（交互式工作负载）：
```bash
vllm serve MODEL \
  --max-num-seqs 64 \
  --gpu-memory-utilization 0.85 \
  --enable-chunked-prefill
```

**内存受限**（40GB GPU 运行 70B 模型）：
```bash
vllm serve TheBloke/Llama-2-70B-AWQ \
  --quantization awq \
  --tensor-parallel-size 1 \
  --gpu-memory-utilization 0.95 \
  --max-model-len 4096
```

## 健康检查和监控

**健康检查端点**：
```bash
curl http://localhost:8000/health
# 返回：{"status": "ok"}
```

**就绪检查**（等待模型加载）：
```bash
#!/bin/bash
until curl -f http://localhost:8000/health; do
    echo "Waiting for vLLM to be ready..."
    sleep 5
done
echo "vLLM is ready!"
```

**Prometheus 抓取**：
```yaml
# prometheus.yml
scrape_configs:
  - job_name: 'vllm'
    static_configs:
      - targets: ['localhost:9090']
    metrics_path: '/metrics'
    scrape_interval: 15s
```

**Grafana 仪表板**（关键指标）：
- 每秒请求数：`rate(vllm_request_success_total[5m])`
- TTFT p50：`histogram_quantile(0.5, vllm_time_to_first_token_seconds_bucket)`
- TTFT p99：`histogram_quantile(0.99, vllm_time_to_first_token_seconds_bucket)`
- GPU 缓存使用：`vllm_gpu_cache_usage_perc`
- 活跃请求：`vllm_num_requests_running`
