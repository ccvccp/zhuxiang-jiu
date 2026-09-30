# 73号·数字人 GPU 轨——AutoDL 实例开通与 LivePortrait 部署操作手册

> 2026-09-30 | 配套: `docs/73号_数字人GPU轨_P1启动评估方案.md` + `backend/build_dh_dev.py`
> 适用: 首次 GPU 机开通到跑通第一条 dh_oral 口播视频的全流程

## 〇、前置条件与总览

- AutoDL 账号（手机号注册 + 实名认证）+ 预算 **50 元级**（方案第八节）
- 链路总览: AutoDL 有卡实例 ← LivePortrait 权重/环境 ← 项目代码(backend) ←
  `build_dh_dev.py`(拉生产 storyboard → 78号 TTS → LivePortrait → attach)
- 成本口径: 无卡模式装环境(约 0.1 元/时) / A10 有卡推理(约 2-3 元/时,
  15s 口播单条约 5-10 分钟 ≈ **0.5 元/条**)

## 一、AutoDL 实例开通

### 1. 注册与认证
1. 浏览器打开 `autodl.com` → 手机号注册（或微信扫码）
2. 控制台右上角「实名认证」→ 按提示完成（个人认证即可, 平台合规要求）

### 2. 充值
- 控制台「充值」→ 微信/支付宝 → **建议首充 50-100 元**（按量, 无月租压力）

### 3. 租用实例（关键参数）
「算力市场」选实例:

| 项 | 推荐值 | 说明 |
|---|---|---|
| GPU | **NVIDIA A10 24G** | 方案指定；缺货时可选 **RTX 4090 24G**（推理性能更佳, 同价档） |
| 地域 | 西北/华北任一 A10 有货区 | 按实时库存 |
| 镜像 | **PyTorch 2.1+ / CUDA 12.1 / Python 3.10** | LivePortrait 官方要求 PyTorch≥2.0；镜像自带免装 |
| 系统盘 | 默认 50G | LivePortrait 权重约 10G+ 环境 10G, 够用 |
| 计费 | **按量计费** | 不要选包周/包月（规模门未过, 见方案 §五） |

> ⚠ 铁律: **先「无卡模式开机」装环境**（约 0.1 元/时）, 环境就绪后关机,
> 真要推理时再「有卡模式开机」——按量计费下无卡调试是最省姿势。

### 4. 连接实例
AutoDL 控制台「容器服务」→ 复制 SSH 登录指令（形如
`ssh -p 30xxx root@region-x.autodl.com`）, 本地终端直连;
或用平台网页版 JupyterLab/Shell。

## 二、LivePortrait 环境部署（无卡模式下进行）

### 1. 下载源码（学术加速）
```bash
# AutoDL 学术加速(GitHub 直连慢)
source /etc/network_turbo
git clone https://github.com/KwaiVGI/LivePortrait.git
cd LivePortrait
unset /etc/network_turbo   # clone 完关闭加速(防 pip 走代理)
```

### 2. 安装依赖（镜像自带 PyTorch, 只补外围）
```bash
pip install -r requirements.txt
# 慢可换源: pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
```

### 3. 下载权重（HF 国内镜像——直连 HuggingFace 基本不可用）
```bash
# v1.5.1 版本(含 audio-driven 口型模块; 权重结构与 tag 以官方 README 为准)
export HF_ENDPOINT=https://hf-mirror.com
pip install -U huggingface_hub
huggingface-cli download KwaiVGI/LivePortrait --local-dir pretrained_weights
```
- 权重约 10G, 放 `LivePortrait/pretrained_weights/`
- **audio-driven 需 v1.5 及以上的额外音频模块权重**——若上述仓库缺
  v1.5 音频权重, 按官方 README「v1.5」小节补齐（以官方说明为准）

### 4. 首跑官方 demo 验证（无卡模式可跑 CPU 慢验, 建议直接有卡验）
```bash
# 视频驱动(基础验证)
python inference.py -s assets/examples/source/s6.jpg \
  -d assets/examples/driving/d6.mp4
# 音频驱动口播(v1.5 口径——正式目标链路)
python inference.py -s assets/examples/source/s6.png \
  -a assets/examples/driving/s6.wav --flag_lip_zeros
```
产物在 `./animations/` 目录——两条命令都出片即环境就绪。

## 三、项目对接（build_dh_dev.py 链路）

### 1. 上传项目代码（backend 目录）
```bash
# 本地执行(Windows): 只传渲染/数字人链路所需文件, 精简上传
scp -r d:/网站架构设计/zhuxiang-jiu/backend root@region-x.autodl.com:/root/
```
（或 `git clone` 仓库到实例——二选一; backend 全量不大, scp 即可）

### 2. 安装后端最小依赖
build_dh_dev 链路 import: sv73_script/render/digital_human/pipeline →
llm_client(78号 TTS HTTP) → 需:
```bash
pip install httpx pydantic -i https://pypi.tuna.tsinghua.edu.cn/simple
```
（跑 `python build_dh_dev.py --help` 报 ModuleNotFoundError 什么补什么,
  无需全量 requirements.txt）

### 3. 环境变量配置（写入 ~/.bashrc 或执行时注入）
```bash
export SV73_DH_MODE=real                          # 数字人推理开
export SV73_LIVEPORTRAIT_DIR=/root/LivePortrait    # 仓库根
export SV73_TTS_MODE=on                            # 78号竹语音轨
export SV73_DH_IMAGE=/root/backend/assets/ip/ip-square.png   # P1 占位基准图
export SV73_TOKEN=<生产 accessToken>                # 或运行时 SSH 管道自动取
# export SV73_DH_CMD="..."                         # 命令校准后按需覆盖
```

### 4. 分两步验收（同 21 轮联调范式——先干跑再实弹）
```bash
cd /root/backend
# 第一步: mock 干跑(不调 GPU, 验证"拉 storyboard→TTS→链路通")
export SV73_DH_MODE=mock
python build_dh_dev.py sv73_<剧本号>
# 第二步: real 实弹(有卡模式开机后)
export SV73_DH_MODE=real
python build_dh_dev.py sv73_<剧本号>
```

### 5. 推理命令校准（21 轮联调教训——stderr 留档校准）
`SV73_DH_MODE=real` 首跑若失败, 脚本会打印 LivePortrait stderr 尾
400 字——按报错校准命令模板并覆盖:
```bash
export SV73_DH_CMD="python inference.py -s {image} -a {audio} \
  --flag_lip_zeros --output_dir {outdir}"
```
占位符契约(改模板必留): `{image}`=基准图, `{audio}`=TTS wav,
`{outdir}`=产物目录, 产物须落在 `<SV73_VIDEO_DIR>/<scriptId>_dh.mp4`
（推理实际输出文件名不同时, 在命令里加 `--flag_...`/mv 后缀对齐,
或调整 SV73_DH_CMD 使产物名匹配）。

## 四、成本控制规范

| 场景 | 操作 |
|---|---|
| 装环境/改代码/看日志 | **无卡模式开机**（约 0.1 元/时） |
| 推理出片 | 有卡模式开机 → 跑完**立即关机** |
| 长期不用 | 关机即可（数据盘保留, 下次秒开; 关机不计费, 只收少量盘费） |
| 规模化信号 | 周产量 > 20 条 → 评估包周/包月（方案 §五规模门, 78号同款算式） |

## 五、常见问题

1. **HuggingFace 下载失败/极慢** → 必须带 `HF_ENDPOINT=https://hf-mirror.com`
2. **GitHub clone 慢** → `source /etc/network_turbo`（AutoDL 学术加速, 用完 unset）
3. **显存不足（CUDA OOM）** → A10 24G 正常不会; 若用小卡, 推理参数降分辨率（官方 README 显存段）
4. **推理超时** → 默认 `DH_TIMEOUT_SECONDS=900`; 慢实例可 env 调大
5. **attach 回填 401** → token 过期: 重新 SSH 管道取或手动 export SV73_TOKEN
6. **口型不同步/质量差** → 属模型/素材调优项, 不阻塞链路; 正式竹小妹
   基准图定版后（正面/半身/稳定光照）重验

## 六、验收清单

- [ ] AutoDL 实例开通 + 充值（首月 50 元级预算上限）
- [ ] LivePortrait 两条 demo 命令出片（视频驱动 + 音频驱动）
- [ ] `build_dh_dev.py` mock 干跑通过（attach 回填成功）
- [ ] `SV73_DH_MODE=real` 实弹一条 dh_oral 口播 mp4（含 attach + 36号 content 可见）
- [ ] 单条 GPU 成本核（目标 ≤ 1 元/条, 方案 §五成本门）
- [ ] 关机规范执行（跑完即关, 下次无卡维护）
