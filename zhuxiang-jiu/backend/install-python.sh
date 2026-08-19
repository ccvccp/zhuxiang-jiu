#!/usr/bin/env bash
# ============================================================
#  Python 3.11+ 环境自动检测与安装 + 一键测试 (Linux 版)
#  适用于: 竹香酒官网 · AI决策筹划模块(29) 后端
#  自包含: 自动生成 requirements.txt 和 pytest.ini
#  用法: chmod +x install-python.sh && ./install-python.sh
# ============================================================

set -euo pipefail

BACKEND_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# 颜色输出
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

info()  { echo -e "${BLUE}  $1${NC}"; }
ok()    { echo -e "${GREEN}  $1${NC}"; }
warn()  { echo -e "${YELLOW}  $1${NC}"; }
fail()  { echo -e "${RED}  $1${NC}" >&2; }

echo "============================================"
echo "  Python 3.11+ 环境检测与一键测试 (Linux)"
echo "  AI决策筹划模块(29) 后端"
echo "============================================"
echo ""

# ---------- [1/6] 检测已有 Python ----------
echo -e "${BLUE}[1/6]${NC} 检测已安装的 Python..."
PYTHON_CMD=""
PYTHON_VERSION=""

for cmd in python3 python; do
    if command -v "$cmd" &>/dev/null; then
        PYTHON_VERSION="$("$cmd" --version 2>&1)"
        if [[ "$PYTHON_VERSION" == *" 3."* ]]; then
            PYTHON_CMD="$cmd"
            break
        fi
    fi
done

if [[ -n "$PYTHON_CMD" ]]; then
    echo -e "${GREEN}  已安装: $PYTHON_VERSION${NC}"
    echo -e "${BLUE}  命令: $PYTHON_CMD${NC}"

    # 校验版本 >= 3.11
    VER_NUM="${PYTHON_VERSION#Python }"
    MAJOR="${VER_NUM%%.*}"
    MINOR="${VER_NUM#*.}"
    MINOR="${MINOR%%.*}"

    if [[ "$MAJOR" -lt 3 ]] || { [[ "$MAJOR" -eq 3 ]] && [[ "$MINOR" -lt 11 ]]; }; then
        warn "Python $VER_NUM 版本过低,需要 3.11+"
        warn "尝试安装最新版..."
        PYTHON_CMD=""
    else
        ok "版本满足要求 (>= 3.11),跳过安装"
        echo ""
    fi
fi

# ---------- [2/6] 检测包管理器并安装 Python ----------
if [[ -z "$PYTHON_CMD" ]]; then
    echo -e "${BLUE}[2/6]${NC} 检测包管理器并安装 Python..."
    echo ""

    if command -v apt-get &>/dev/null; then
        # Debian/Ubuntu
        echo "  系统类型: Debian/Ubuntu (apt-get)"
        echo "  更新软件源..."
        sudo apt-get update -qq
        echo "  安装 Python 3.12 及开发工具..."
        sudo apt-get install -y -qq python3.12 python3.12-venv python3-pip
        PYTHON_CMD="python3.12"

    elif command -v dnf &>/dev/null; then
        # RHEL/Fedora/CentOS
        echo "  系统类型: RHEL/Fedora (dnf)"
        sudo dnf install -y -q python3 python3-pip
        PYTHON_CMD="python3"

    elif command -v yum &>/dev/null; then
        # CentOS 7
        echo "  系统类型: CentOS (yum)"
        sudo yum install -y -q python3 python3-pip
        PYTHON_CMD="python3"

    elif command -v apk &>/dev/null; then
        # Alpine
        echo "  系统类型: Alpine (apk)"
        sudo apk add --no-cache python3 py3-pip
        PYTHON_CMD="python3"

    elif command -v zypper &>/dev/null; then
        # openSUSE
        echo "  系统类型: openSUSE (zypper)"
        sudo zypper install -y -q python3 python3-pip
        PYTHON_CMD="python3"

    elif command -v pacman &>/dev/null; then
        # Arch
        echo "  系统类型: Arch (pacman)"
        sudo pacman -S --noconfirm python python-pip
        PYTHON_CMD="python"

    elif command -v brew &>/dev/null; then
        # macOS Homebrew
        echo "  系统类型: macOS (brew)"
        brew install python@3.12
        PYTHON_CMD="$(brew --prefix python@3.12)/bin/python3"

    else
        fail "无法识别的包管理器,请手动安装 Python 3.11+:"
        fail "  apt-get install python3 (Debian/Ubuntu)"
        fail "  dnf install python3    (Fedora/RHEL)"
        fail "  brew install python3   (macOS)"
        exit 1
    fi

    echo ""
    echo -e "${BLUE}[3/6]${NC} 验证安装..."
    if ! command -v "$PYTHON_CMD" &>/dev/null; then
        # 回退到 python3
        if command -v python3 &>/dev/null; then
            PYTHON_CMD="python3"
        else
            fail "Python 安装后仍无法调用"
            fail "请手动安装: https://www.python.org/downloads/"
            exit 1
        fi
    fi
    PYTHON_VERSION="$("$PYTHON_CMD" --version 2>&1)"
    ok "验证成功: $PYTHON_VERSION (命令: $PYTHON_CMD)"
    echo ""
fi

# 跳过 [2/6] [3/6] (已有 Python 时直接到这里)
if [[ -z "${PYTHON_VERSION:-}" ]]; then
    echo -e "${BLUE}[2/6]${NC} 跳过 (已安装)"
    echo -e "${BLUE}[3/6]${NC} 跳过 (已安装)"
    echo ""
fi

# ---------- [4/6] 生成配置文件 ----------
echo -e "${BLUE}[4/6]${NC} 检查配置文件..."
echo ""

# 生成 requirements.txt (如果不存在)
if [[ ! -f "$BACKEND_DIR/requirements.txt" ]]; then
    info "生成 requirements.txt..."
    cat > "$BACKEND_DIR/requirements.txt" << 'REQEOF'
fastapi==0.115.0
uvicorn[standard]==0.32.0
pydantic==2.9.0
python-jose[cryptography]==3.3.0
passlib[bcrypt]==1.7.4

# 测试依赖
pytest==8.3.0
httpx==0.27.0
pytest-cov==5.0.0
pytest-asyncio==0.24.0
REQEOF
    ok "requirements.txt 已生成"
else
    info "requirements.txt 已存在,跳过"
fi

# 生成 pytest.ini (如果不存在)
if [[ ! -f "$BACKEND_DIR/pytest.ini" ]]; then
    info "生成 pytest.ini..."
    cat > "$BACKEND_DIR/pytest.ini" << 'INIEOF'
[pytest]
minversion = 8.0
testpaths = .
python_files = test_*.py
python_classes = Test*
python_functions = test_*
addopts = -v --tb=short --strict-markers --color=yes -ra
log_cli = true
log_cli_level = INFO
log_cli_format = %(asctime)s [%(levelname)s] %(message)s
log_cli_date_format = %H:%M:%S
asyncio_mode = auto
filterwarnings =
    ignore::DeprecationWarning
    ignore::PendingDeprecationWarning
INIEOF
    ok "pytest.ini 已生成"
else
    info "pytest.ini 已存在,跳过"
fi
echo ""

# ---------- [5/6] 安装依赖 ----------
echo -e "${BLUE}[5/6]${NC} 安装项目依赖..."
echo ""

# 创建虚拟环境 (避免污染系统 Python)
VENV_DIR="$BACKEND_DIR/.venv"
if [[ ! -d "$VENV_DIR" ]]; then
    info "创建虚拟环境..."
    "$PYTHON_CMD" -m venv "$VENV_DIR"
    ok "虚拟环境已创建: $VENV_DIR"
fi

# 激活虚拟环境
# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"
PYTHON_CMD="python"

info "升级 pip..."
pip install --upgrade pip -q

info "安装依赖 (从 requirements.txt)..."
if ! pip install -r "$BACKEND_DIR/requirements.txt" -q; then
    warn "批量安装失败,尝试逐个安装..."
    pip install -q fastapi uvicorn pydantic
    pip install -q python-jose passlib
    pip install -q pytest httpx pytest-cov pytest-asyncio
fi

ok "依赖安装完成"
echo ""

# ---------- [6/6] 运行测试 ----------
echo -e "${BLUE}[6/6]${NC} 运行单元测试..."
echo ""

cd "$BACKEND_DIR"

echo "  执行: pytest -v"
echo "  配置: pytest.ini (asyncio_mode=auto)"
echo ""

if pytest -v; then
    echo ""
    ok "全部测试通过!"
else
    echo ""
    warn "部分测试未通过,请检查上方输出"
fi

echo ""
echo "============================================"
echo "  环境安装与测试完成"
echo "  Python: $PYTHON_VERSION"
echo "  venv:   $VENV_DIR"
echo "  目录:   $BACKEND_DIR"
echo ""
echo "  后续运行:"
echo "    source $VENV_DIR/bin/activate"
echo "    pytest -v"
echo "============================================"
