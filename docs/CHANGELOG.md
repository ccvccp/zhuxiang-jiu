# 变更日志 (Changelog)

## [2026-08-19] pre-commit 钩子增强版 awk 正则字面量修复

### 概述

修复 pre-commit 钩子和基准脚本中的旧版 awk 括号检查逻辑，同步增强版正则字面量识别功能，消除假阳性拦截 Bug。

### 修复的 Bug

#### Bug: 正则字面量内括号被误计导致假阳性拦截

- **严重级别**: 高
- **影响范围**: pre-commit 钩子（生产环境）+ 基准脚本
- **症状**: 包含正则字面量的 JS 文件被错误拦截
  - `var re = /\(/g;` 中的 `(` 被误计 → 括号不匹配 → 提交被阻止
  - `var re = /\{n\}/g;` 中的 `{` 被误计 → 花括号不匹配 → 提交被阻止
- **根因**: 旧版 awk 状态机缺少正则字面量识别（`in_re`/`in_class`/`prev` 跟踪），将正则内的 `/` 视为除法运算符，导致正则内的括号被当作代码括号计数
- **修复方案**: 同步增强版 awk 状态机，增加 3 个新状态：
  - `in_re` — 正则字面量模式（`/` 在 `=`/`(`/`,`/`;` 等上下文后进入）
  - `in_class` — 正则字符类模式（`[...]` 在正则内不计数为方括号）
  - `prev` — 跟踪最后一个非空白字符，用于判断 `/` 是正则还是除法

### 修改的文件

| 文件 | 修改类型 | 变更内容 |
|------|----------|----------|
| `scripts/pre-commit` | 修复 | `run_bracket_check()` 旧版 → 增强版（+`in_re`/`in_class`/`prev`） |
| `scripts/bench-pre-commit.sh` | 修复 | `check_new()` 旧版 → 增强版（+`in_re`/`in_class`/`prev`） |
| `.git/hooks/pre-commit` | 同步 | 生产钩子同步更新为增强版 |
| `scripts/test-lib.sh` | 新增 | 测试共享库，提供 `tl_check_brackets()` 等通用函数 |
| `scripts/test-bracket-check.sh` | 重构 | 引用 test-lib.sh，消除重复代码 |
| `scripts/test-bracket-boundary.sh` | 重构 | 引用 test-lib.sh，消除重复代码 |
| `scripts/test-pre-commit.sh` | 重构 | 引用 test-lib.sh，消除重复代码 |
| `README.md` | 更新 | 测试结果新增正则字面量修复验证行 + 修复记录 |

### 关键修复点

#### 1. awk 状态机增强（核心修复）

旧版（无正则识别）：
```awk
BEGIN { in_str=0; in_block=0; str_ch=""; po=pc=bo=bc=so=sc=0 }
# ... 缺少 in_re/in_class/prev 逻辑
if (c == sq || c == "\"" || c == "`") { in_str=1; str_ch=c; i++; continue }
if (c == "(") po++
# ...
```

增强版（含正则识别）：
```awk
BEGIN { in_str=0; in_block=0; in_re=0; in_class=0; str_ch=""; prev=""; po=pc=bo=bc=so=sc=0 }
# ... 新增 in_re/in_class 状态处理
if (in_re) {
    if (in_class) { ... }       # 正则字符类内不计数
    if (c == "/") { in_re=0 }   # 正则结束
}
if (c == "/") {
    is_re = (prev == "=" || prev == "(" || ...)  # 上下文判断
    if (is_re) { in_re=1 }      # 进入正则模式
}
if (c == "(") { po++; prev=c } # 记录 prev 用于后续判断
```

#### 2. 测试共享库提取（代码重构）

- 提取 `scripts/test-lib.sh`，提供 6 个通用函数
- 3 个测试脚本消除 ~180 行重复代码
- 统一使用增强版 awk 状态机

#### 3. 4 处实现统一

| 位置 | 函数名 | 版本 |
|------|--------|------|
| `scripts/test-lib.sh` | `tl_check_brackets()` | 增强版 |
| `scripts/pre-commit` | `run_bracket_check()` | 增强版（同步） |
| `scripts/bench-pre-commit.sh` | `check_new()` | 增强版（同步） |
| `.git/hooks/pre-commit` | `run_bracket_check()` | 增强版（同步） |

### 测试验证

| 套件 | 用例数 | 结果 |
|------|--------|------|
| bracket-check (TC1-TC15) | 15 | ✅ ALL PASS |
| bracket-boundary (BC1-BC20) | 20 | ✅ ALL PASS |
| pre-commit CI flow (TC1-TC5) | 5 | ✅ ALL PASS |
| **总计** | **40** | **100%** |

关键验证用例：
- BC1: `var re = /\(/g;` → `()1/1` 正确（旧版误计为 `()2/1`）
- BC2: `var re = /\{n\}/g;` → `{}1/1` 正确（旧版误计为 `{}2/1`）
- BC3: `${obj.method()}` → `()1/1` 正确

### Git 提交历史

```
d1209fa fix: sync enhanced awk with regex literal support to pre-commit hook and bench (40/40 PASS)
12500d5 refactor: extract test-lib.sh shared library, deduplicate 3 test scripts (40/40 PASS)
b75cd1f test: add P0 boundary condition test (BC1-BC20, 20/20 PASS, regex/template/BOM/CRLF)
790bd90 refactor: rewrite bench-pre-commit.sh for old-vs-new comparison (NEW 81% faster)
59333f3 refactor: optimize pre-commit hook with awk string-aware bracket check (15/15 PASS)
1650ddc fix: pre-commit hook read EOF handling and add CI unit test
5ecba33 chore: add gitignore and pre-commit CI hook scripts
```

### 性能影响

| 指标 | 修复前 | 修复后 | 变化 |
|------|--------|--------|------|
| 平均/文件 | 97 ms | ~100 ms | +3%（正则识别开销） |
| 子进程/文件 | 1 | 1 | 无变化 |
| 假阳性率 | 高（正则字面量误计） | 0（消除） | 显著提升 |

### 向后兼容性

- **无破坏性变更**: 增强版完全兼容旧版行为
- 正则字面量内的括号不再计数（Bug 修复）
- 代码中的括号计数行为不变
- 日志格式不变
