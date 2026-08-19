# ============================================================
#  Dockerfile · AI决策筹划模块(29) 后端环境 + 测试
#  竹香酒官网 · Python 3.12 + FastAPI + pytest
#  构建: docker build -t zhuxiang-decision-api:1.0 .
#  运行: docker run --rm -p 8000:8000 zhuxiang-decision-api:1.0
#  测试: docker run --rm zhuxiang-decision-api:1.0 pytest -v
# ============================================================

FROM python:3.12-slim

LABEL org.opencontainers.image.title="竹香酒-AI决策筹划模块29"
LABEL org.opencontainers.image.description="6层架构(感知→知识→决策→编排→执行→反馈) FastAPI后端+测试"
LABEL org.opencontainers.image.version="1.0"

# 设置工作目录
WORKDIR /app

# 安装系统依赖(curl用于健康检查)
RUN apt-get update -qq && \
    apt-get install -y --no-install-recommends -qq curl && \
    rm -rf /var/lib/apt/lists/*

# 先复制依赖文件(利用 Docker 层缓存)
COPY requirements.txt pytest.ini ./

# 安装 Python 依赖
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# 复制源码
COPY main.py models.py ./
COPY test_risk_control.py ./

# 复制测试用例目录(后续扩展用)
# COPY tests/ ./tests/

# 设置环境变量
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    API_MODE=mock

# 暴露端口
EXPOSE 8000

# 健康检查(每30秒检查一次,超时5秒,重试3次)
HEALTHCHECK --interval=30s --timeout=5s --retries=3 --start-period=10s \
    CMD curl -sf http://localhost:8000/api/decision/health || exit 1

# 默认启动 FastAPI 服务
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
