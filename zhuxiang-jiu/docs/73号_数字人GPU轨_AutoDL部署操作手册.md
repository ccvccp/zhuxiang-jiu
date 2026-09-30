# 73号·数字人 GPU 轨——AutoDL 实例开通与 SadTalker 部署操作手册

> 2026-09-30 | 配套: `docs/73号_数字人GPU轨_P1启动评估方案.md` + `backend/build_dh_dev.py`
> **方案修正实录**: 原定 LivePortrait——GPU 机实证官方 main 无 audio-driven 口型
> (仅视频驱动); 按 docx 方案 B 原文列名的 **SadTalker**(单图+音频→口播)落地,
> RTX 5090D 出片实证。本文全程为 2026-09-30 实际部署实录。

## 〇、前置条件与总览

- AutoDL 账号(手机号注册+实名认证) + 预算 **50 元级**(实测充 100 元)
- 链路: AutoDL 实例(RTX 5090D) ← SadTalker+权重 ← backend 代码 ←
  build_dh_dev.py(拉生产 storyboard→TTS→SadTalker→attach)
- 成本实录: 5090D **￥1.88-2.88/时**; torch 2.7.1+cu128(Blackwell 必需,
  旧 torch 2.1.2 报 sm_120 no kernel image)

## 一、AutoDL 实例开通(2026-09-30 实录)

### 关键参数(与原方案差异)
| 项 | 实录值 | 说明 |
|---|---|---|
| GPU | **RTX 5090D 32G**(西北B区) | A10 平台无货——AutoDL 以消费卡为主; 4090D 有货但抢手 |
| 镜像 | PyTorch 2.1.2/CUDA 11.8(Python 3.10) | ⚠ **5090D(Blackwell sm_120) 必须升级 torch 2.7.1+cu128** |
| 计费 | 按量 ￥1.88-2.88/时 | 微信注册需绑手机号; 实名+充值后可租 |

### 人机协作分工(网页防自动化)
- 自动化可做: 页面导航/状态读取/实例监控
- **必须人工**: 注册验证码/实名信息/支付扫码/**租实例**(平台组件防
  自动化点击, 同 xhs 安全盾性质)——交接完成

## 二、SadTalker 环境部署(2026-09-30 实录)

### 1. 源码与权重
```bash
source /etc/network_turbo   # AutoDL 学术加速(GitHub/HF 直连基本不可用)
cd /root/autodl-tmp
git clone --depth 1 https://github.com/OpenTalker/SadTalker.git
cd SadTalker && bash scripts/download_models.sh   # ~4G, 250MB/s 飞快
```

### 2. Python 3.12 环境(项目代码 3.12 惯例——两个 3.10 坑)
- 坑 1: `from datetime import UTC`(3.11+) → **conda 建 py312 env**,
  或 3.10 打 sitecustomize 垫片(临时)
- 坑 2: f-string 跨行(PEP 701, 3.12 语法) → 3.10 直接 SyntaxError
- 结论: `conda create -n py312 python=3.12` + 全套装到该环境

### 3. torch 升级(Blackwell sm_120 必需——四坑实录)
```bash
# ✅ 最终成功方案: 上交镜像整体替换 pypi 索引(pep503 完整索引,
#    torch/torchvision/torchaudio + nvidia-* 依赖全有 cp312 wheel)
P=/root/miniconda3/envs/py312/bin
$P/pip uninstall -y torch torchvision torchaudio -q
$P/pip install torch==2.7.1 torchvision==0.22.1 torchaudio==2.7.1 \
  --index-url https://mirror.sjtu.edu.cn/pytorch-wheels/cu128/
```
| 踩坑顺序(2026-09-30 实录) | 结果 |
|---|---|
| 镜像自带 torch 2.1.2+cu118 | `no kernel image`(无 sm_120) |
| pypi 官方 torch 2.7.1(默认 cu126 build) | 同样 `no kernel image`——pypi 无 +cu128 |
| 官方 download.pytorch.org | 龟速(24kB/s); 开学术加速后 403 被拒 |
| 清华 pytorch-wheels/cu128 + `-f` find-links | **404 无此目录**, `-i` pypi 竞争下仍装成 cu126(假成功——`pip list` 看版本号带 +cu128 才可信, `torch.version.cuda` 须 12.8) |
| 上交 `--index-url` 整体替换 | **成功**: `torch 2.7.1+cu128` + 5090D matmul OK |

验证(必须三件套全过): `torch.version.cuda == '12.8'` +
`get_device_capability() == (12, 0)` + GPU matmul 无 `no kernel image`。

### 4. 依赖坑链实录(numpy 2.x × SadTalker 老代码)
| 坑 | 修法 |
|---|---|
| `np.VisibleDeprecationWarning` 移除 | sed → `DeprecationWarning` |
| `np.float` 别名移除 | sed → `float` |
| numpy 2.x `float(数组)` 严格化(只许 0 维) | **ravel 补丁 4 处**: `float(X)` → `float(np.ravel(X)[0])`——face3d/util/preprocess.py L46/L48/L101 + utils/preprocess.py L148(hsplit 切片) |
| basicsr sdist 拉 tb-nightly 失败 | `pip install basicsr --no-deps`(wheel 本体无此依赖) |
| basicsr × torchvision 0.22 `functional_tensor` | sed → `torchvision.transforms.functional` |
| facexlib/gfpgan 依赖链 | `--no-deps` 装+复制法跨环境 |
| py312 二进制包不可从 3.10 复制(cp310 ABI) | `pip install opencv-python-headless "moviepy<2" scipy librosa soundfile pydub safetensors scikit-image PyYAML matplotlib imageio-ffmpeg tqdm kornia einops yacs face-alignment transformers`(moviepy 锁 <2 保 `moviepy.editor` 老 API) |
| `sh: ffmpeg: not found`(moviepy 收尾) | `ln -sf $(python -c "import imageio_ffmpeg;print(imageio_ffmpeg.get_ffmpeg_exe())") /usr/local/bin/ffmpeg` |
| 产物落点认知修正 | 最终 mp4 落 **result_dir 根级** `{timestamp}.mp4`(预处理时间戳目录另建, mp4 亦存一份)——服务模板用 `find {outdir} -maxdepth 2 -name '*.mp4' -printf '%T@\t%p\n' | sort -rn | head -1` 取最新对齐 `{out}`(时间排序防复用污染) |

## 三、项目对接(build_dh_dev.py 链路)

### 环境变量(算力机)
```bash
export SV73_DH_MODE=real
export SV73_SADTALKER_DIR=/root/autodl-tmp/SadTalker
export SV73_TTS_MODE=on
export SV73_DH_IMAGE=<口播基准图>(P1 占位: 金鹿 IP 图)
export SV73_TOKEN=<生产 accessToken>(或 SSH 管道自动取)
```

### 代码上传(两通道实录)
- 单文件: exec+base64(`adh_upload.py`——SFTP 子系统受限不可用)
- 全目录: tar.gz→base64→分块 printf 拼接(`adh_put.py`, **必须 md5 双端校验**)

### 验收实录(2026-09-30/10-01, 服务级先行)
- **服务级 mock 干跑**: importlib 独立加载 `services/sv73_digital_human_service.py`(纯标准库, 绕开 services/__init__.py 巨网)——off 铁律拦截 ✓ + mock 登记产物 `{scriptId}_dh.mp4` 对齐 ✓
- **服务级 real 实弹**: `SV73_DH_MODE=real` + `SV73_SADTALKER_DIR` →
  subprocess `bash -c`(py312 python + inference.py) → 5090D 全链
  (mel 84/84→audio2exp 9/9→Face Renderer 42/42→seamlessClone 84/84)→
  产物 `DHREAL002_dh.mp4`(778KB, 800x1200/25fps)——**服务全链 E2E PASS**
- **build_dh_dev.py 全链**: 需生产 `SV73_TOKEN`(TTS 走 78号竹语)——
  留待生产凭证就绪时首跑; 服务级已验证的即其视频主轨子集

```bash
SV73_DH_MODE=mock  python build_dh_dev.py <scriptId>   # 干跑(生产 token)
SV73_DH_MODE=real  python build_dh_dev.py <scriptId>   # 5090D 实弹(生产 token)
```

### 生产全链首跑实录(2026-10-01, 拆链编排——build_dh_dev 四步分解)

> GPU 机 import 巨网(render_service→36号 promo 链)不可控, 按分段铁律
> 拆链: 本地编排(登记/TTS/attach) + 算力机只跑 GPU 推理(服务级形态)。

0. **生产镜像更新**(前置): 生产容器代码旧无 dh_oral(pipeline/run 409)——
   diff 5e05da4..055924d 取 5 文件(scp services/sv73_{script,pipeline,
   render,digital_human}_service.py + Dockerfile)→ `docker compose up -d
   --build backend`(备份 .bak-dh101 回滚通道; ffmpeg 层生效 ✓)
1. **生产 pipeline/run**(template=dh_oral): `sv73_878abd957729`
   (glm-4-flash 真实轨, 15s/1 镜, contentId=43 shadow/pending 三审闸门)
2. **本地 TTS**(78号竹语): LLM_API_KEY 经 SSH 容器管道注入本进程
   (printenv, 不落盘)→ `sv73_878abd957729_tts.wav`(703KB)
3. **算力机推理**(real E2E): 基准图+wav 分块 base64+md5 双端校验上传 →
   importlib 独立加载服务 → 5090D → `{sid}_dh.mp4`(**14.6s/800x1200/
   25fps/3.4MB**——与 15s 注册时长对齐)→ 分块下载回本地
4. **attach 回填**: POST /api/sv73/render/attach → **HTTP 200**,
   contentId=43, renderSource=devmachine ✓(后续 36号人工三审→publish)

**基准图铁律实证**: 金鹿 IP 角标(瑞兽图形)**无人脸**——SadTalker
landmark 前置即败(`can not detect the landmark`)。数字人基准图必须为
**含清晰人脸的正面照**。

**竹小妹基准图定版(2026-10-01)**: `assets/ip/zhuxiaomei_front.jpg`
(853×1515 正面照)——服务默认 `DH_IMAGE` 换代, dh_batch 每次跑批前
自动上传校验(图随代码版本走); GPU 侧 landmark 实测随下次跑批首验
(失败路径明确报错, 不烧盲跑)。

## 四、成本控制规范

| 场景 | 操作 |
|---|---|
| 装环境/改代码 | 无卡模式开机(约 0.1 元/时) |
| 推理出片 | 有卡开机→跑完立即关机 |
| 长期不用 | 关机(数据盘保留, 15 天不关机才释放) |
| 规模化 | 周产 >20 条再评估包周(方案 §五规模门) |

## 四点五、自动开/关机编排(2026-10-01 无人值守批量实录)

**API 形态实证(2026-10-01)**: AutoDL 开发者 API(api.autodl.com +
开发者 Token, 控制台→设置→开发者Token, 长期有效)实例面**仅覆盖
"容器实例 Pro"**(autodl-cli 开源项目全端点 = /api/v1/dev/instance/
pro/*); **网页控制台租的普通实例(本项目 5090D)不在 API 体系**(pro/list
空实证)。故:
- **当前半自动**(普通实例): 控制台人工开机(约 10 秒)→ dh_batch
  全自动跑批+自动关机; `wallet/balance` 余额止损检查 Token 即用
- **Pro 全自动迁移(2026-10-01 实施受阻实录)**:
  - ✓ 环境固化: SadTalker 挪入系统盘(/root/SadTalker 2.5G, torch
    cu128 冒烟过), 关机态保存镜像 **zhuxiang-dh-sadtalker**
    (image-c255400f08)——环境随时可复现(普通实例换镜像/Pro create)
  - ✓ 工具就绪: adh_power 新增 images/create/snapshot; dh_batch
    `--pro <uuid>` 模式(snapshot 动态 SSH+服务/图自上传+API 关机)
  - ✗ **create 被平台拒**: 全规格(5090-p/4090D/v-48g/h800)×
    公共/私有镜像均报"无当前资源访问权限"(读接口全通——判定账号
    级 Pro 功能未开放, 非代码/规格问题)。**待 AutoDL 客服确认
    开通**; 开通后 `adh_power.py create --image image-c255400f08
    --gpu 5090-p --disk 10` 即起全环境实例, dh_batch --pro 全链

**adh_power.py**(AutoDL 官方开放 API 工具):
- `python adh_power.py balance|list|status|on|off` —— api.autodl.com,
  Token 注入 `ADH_API_TOKEN` env 或 `ADH_TOKEN_FILE` 指向文件
  (本地 `.adh_token`, 已入 .gitignore); balance 为通用(两种形态)
- 余额实证: **¥126.60**(累计消费 **¥23.40** = 2026-09-30 开通至今
  全部 GPU 成本——部署+两次实弹+生产全链, 远优于预算)

**dh_batch.py**(批量编排器, build_dh_dev 拆链四步正式化):
```bash
python dh_batch.py --sids sv73_a,sv73_b    # 控制台开机后: 跑批→自动关机
python dh_batch.py --sid sv73_x --no-shutdown --min-balance 10
```
- **跑批前置余额止损**(wallet/balance, Token 未配则跳过): 余额 <
  阈值(默认 ¥10)即退出不跑——防中途欠费断机
- 单条流程: 拉 storyboard → 本地竹语 TTS(key 经生产容器管道注入,
  不落盘) → 分块上传(md5 双端校验) → 远端 5090D E2E(importlib
  独立加载) → 分块下载 → attach 生产 → 收尾 shutdown(官方指令)
- 实测: 单条全流程 **~3.5 分钟**, attach 200 ×2(contentId 43/44);
  成本 ¥0.1-0.19/条; 批量摊薄(模型常驻)留 P2

## 五、验收清单(2026-09-30/10-01 实录勾选)

- [x] AutoDL 实例开通(RTX 5090D, 余额 ￥100)
- [x] SadTalker demo 出片(5090D, bus_chinese.wav 实证)
- [x] torch 2.7.1+cu128 GPU kernel 实测(matmul OK——上交镜像方案)
- [x] 全量 backend 上传(md5 校验一致)
- [x] 服务级 mock 干跑通过(off 铁律拦截 + mock 登记对齐)
- [x] SV73_DH_MODE=real 实弹 dh_oral 口播 mp4(DHREAL002_dh.mp4,
      778KB/800x1200/25fps——服务全链 E2E PASS)
- [x] 单条 GPU 成本核: 全链实测 ~2.5-4 分钟/条(含模型加载 60-90s)×
      ￥1.88-2.88/时 ≈ **￥0.08-0.19/条**; 批量摊薄加载后 <￥0.1/条——
      远低于 ≤￥1/条红线, 方案 §五 ROI 触发器成立
- [x] 生产全链首跑(拆链编排): 生产镜像更新(5 文件+compose rebuild)
      → pipeline/run `sv73_878abd957729`(glm-4-flash 轨) → 竹语 TTS
      → 5090D 推理(14.6s mp4/3.4MB) → **attach 200 contentId=43** ✓
- [x] 关机规范执行(2026-10-01 容器内 shutdown 官方指令——dh_batch
      默认收尾同款; 开机自动化 adh_power.py 待开发者 Token 实测)
- [x] 竹小妹正式基准图定版(2026-10-01: assets/ip/zhuxiaomei_front.jpg
      853×1515 正面照——服务默认换代+dh_batch 跑批自动上传校验;
      GPU landmark 实测随下次跑批首验)
