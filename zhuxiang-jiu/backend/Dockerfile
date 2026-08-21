# ============================================================
#  Dockerfile · AI决策筹划模块(29) 后端环境
#  竹香酒官网 · Python 3.12 + FastAPI
#  构建: docker build -t zhuxiang-decision-api:1.0 .
#  运行: docker run --rm -p 8000:8000 zhuxiang-decision-api:1.0
# ============================================================

FROM python:3.12-slim

LABEL org.opencontainers.image.title="竹香酒-AI决策筹划模块29"
LABEL org.opencontainers.image.description="4层架构(core/repositories/services/routes) FastAPI后端"
LABEL org.opencontainers.image.version="1.1"

WORKDIR /app

# 安装系统依赖(curl 用于健康检查)
RUN apt-get update -qq && \
    apt-get install -y --no-install-recommends -qq curl && \
    rm -rf /var/lib/apt/lists/*

# ---- 层 1: 仅复制依赖清单(改动最少, 最大化缓存命中) ----
COPY requirements.txt ./

# ---- 层 2: 安装生产依赖(测试依赖在 requirements-dev.txt, 不进镜像) ----
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# ---- 层 3: 复制业务源码 ----
# 注: 用 COPY src dest 标准写法(复制整个目录, 包括 __init__.py)
COPY main.py models.py ./
COPY core           /app/core
COPY repositories   /app/repositories
COPY routes         /app/routes
COPY services       /app/services
COPY scripts        /app/scripts

# 验证关键文件已 COPY(构建时断言,失败立即报错)
# 用 ls -la 显示实际内容, 方便定位哪个文件丢失
RUN echo "========== /app 内容 ==========" && \
    ls -la /app/ && \
    echo "========== /app/core 内容 ==========" && \
    ls -la /app/core/ && \
    echo "========== /app/repositories 内容 ==========" && \
    ls -la /app/repositories/ && \
    echo "========== /app/routes 内容 ==========" && \
    ls -la /app/routes/ && \
    echo "========== /app/services 内容 ==========" && \
    ls -la /app/services/ && \
    echo "========== /app/scripts 内容 ==========" && \
    ls -la /app/scripts/

# 验证 __init__.py 存在(失败立即报错, 避免运行时才发现)
RUN test -f /app/main.py && \
    test -f /app/core/__init__.py && \
    test -f /app/repositories/__init__.py && \
    test -f /app/routes/__init__.py && \
    test -f /app/services/__init__.py && \
    test -f /app/scripts/__init__.py && \
    echo "[OK] all __init__.py copied" || \
    (echo "[FAIL] missing __init__.py" && exit 1)

# 验证 import 链(构建时测试,失败立即报错,避免运行时才发现)
RUN python -c "from core.config import CORS_ORIGINS; print('core import OK')" && \
    python -c "from repositories.store import _mock_store; print('repositories import OK')" && \
    python -c "from routes import register_decision_routes; print('routes import OK')" && \
    python -c "from services import AgentService; print('services import OK')" && \
    python -c "import main; print('main import OK')"

# 运行时环境
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    API_MODE=mock

EXPOSE 8000

# 健康检查(每 30s 一次, 超时 5s, 重试 3 次, 启动后 30s 开始)
# 注意: docker-compose.yml 的 healthcheck 会覆盖此配置,两处保持一致避免困惑
HEALTHCHECK --interval=30s --timeout=5s --retries=3 --start-period=30s \
    CMD curl -sf http://localhost:8000/api/decision/health || exit 1

# 启动命令(由 docker-compose 覆盖以插入 seed 步骤)
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
