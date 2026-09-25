# 小竹语音全面诊断(千问 qwen3-max, 24h 数据)

# 小竹语音精灵系统全面诊断复查报告

## 一、ASR 维度

### 【诊断】
1. **环境声长转写（烧额度）**  
   - 日志证据：`asr.long_ambient=23/37`，用户外放「桃子观察」被完整转写  
   - 根因分析：KWS 轨仅做唤醒词检测但未做 VAD 过滤即推送全段音频至百炼 ASR。当前架构中 KWS 轨的 VAD 仅为能量门限起段（TH_ON=0.009），无止段逻辑或语义过滤，导致非人声持续送流。  
   - 置信度：高  

2. **wakeword_in_final=0 但 command_done 存在**  
   - 日志证据：`wakeword_in_final=0` vs `points.command_done=2`  
   - 根因分析：唤醒词由前端 KWS 检出后触发状态机进入 S1，服务端 inject vocabulary 热词仅用于提升识别准确率，不强制出现在 final。若唤醒后用户立即说话，ASR 可能将“小竹”与后续指令合并为一句（如“小竹推荐红酒”），而热词未单独成词则不会标记 wakeword_in_final。另可能因 teardownSeg 在 partial 命中后提前结束段，final 未包含唤醒词。  
   - 置信度：中  

3. **ws_asr_error: Cannot call "send" once a close message has been sent**  
   - 日志证据：`2026-09-25 21:30:43 [WARNING] xiaozhu_routes: ws_asr_error: Cannot call "send" once a close message has been sent.`  
   - 根因分析：`xiaozhu_routes` 模块在 WebSocket 已发送 close frame 后仍尝试 send()，说明连接状态机未同步关闭标志，存在竞态。典型于 onclose 回调中未设置 `_ws_closed = True` 或 send 前未校验。  
   - 置信度：高  

4. **auth_failed / closure_1006**  
   - 日志证据：`ws_asr_auth_failed err=(1001, '') recv=empty`、`err=(<CloseCode.ABNORMAL_CLOSURE: 1006>, '')`  
   - 根因分析：建连初期 token 为空（`token_prefix=`），结合 X5 后台冻结特性，推测用户切后台/息屏导致 JS 冻结，WebSocket 握手未完成即被系统杀掉（1006）。auth.ver=2 预热池虽有锁，但首次建连仍可能因上下文丢失而 token 无效。  
   - 置信度：高  

---

### 【优化方案】
- **P0**：KWS 轨增加 **语义级 VAD 过滤**  
  - 改法要点：KWS 轨在 RMS 起段后，需等待至少 0.3s 有效人声（通过短时频谱熵或过零率初筛），且长度 ≤8s 才推送；超长段直接丢弃并 log。  
  - 预期收益：减少 60%+ 无效 ASR 请求，节省成本。  
  - 风险：极低（仅过滤明显非人声）  

- **P1**：修复 WebSocket 状态竞态  
  - 改法要点：在 `xiaozhu_routes.WsAsrHandler` 中，所有 send() 前加 `if self._closed: return`；on_close 中立即置 `_closed=True` 并 cancel pending tasks。  
  - 预期收益：消除 `ws_asr_error` 日志，避免无效 send 异常。  
  - 风险：低  

- **P1**：增强唤醒词落盘逻辑  
  - 改法要点：无论 ASR 是否返回“小竹”，只要 KWS 命中且进入 S1，服务端强制在 final 注入 `{"wakeword": true}` 字段供统计。  
  - 预期收益：解决 `wakeword_in_final=0` 误判问题。  
  - 风险：无  

---

### 【复核】人工预诊信号
- **信号2（环境声长转写）**：✅ **确认**。日志 `long_ambient=23` 与描述一致，属架构缺陷。  
- **信号3（wakeword_in_final=0）**：⚠️ **存疑但倾向合理**。非 bug，是热词注入机制与 ASR 合并策略导致，建议按 P1 方案增强埋点。  
- **信号4（ws_asr_error）**：✅ **确认**。日志明确显示 close 后 send，属代码缺陷。  

---

## 二、连接层维度

### 【诊断】
- **预热连接池 token 失效**  
  - 日志证据：`auth_failed` 三次均 `token_prefix=`，且发生在建连初期  
  - 根因分析：`xz.refAt` 锁防双 iframe 刷新吊销，但未覆盖 **首次加载无 token 场景**。用户从微信外链进入时，auth.ver=2 请求可能因 Cookie 未就绪而返回空 token，导致 WS 握手失败。  
  - 置信度：中  

- **1006 关闭主因是后台冻结**  
  - 日志证据：`closure_1006=2`，结合 X5 物理边界“切后台冻结 JS 杀 WS”  
  - 根因分析：非服务端问题，属客户端环境限制。v=76 自愈机制应覆盖此场景，但日志显示未触发重建（因 session.open=1，可能仅单次会话）。  
  - 置信度：高  

---

### 【优化方案】
- **P1**：首次加载 token 容错  
  - 改法要点：Widget 初始化时若 `refAt` 为空，延迟 500ms 重试 auth.ver=2，最多 2 次；失败则降级提示“请刷新页面”。  
  - 预期收益：降低首次 auth_failed 率。  
  - 风险：低  

- **P2**：增强 visibilitychange 自愈覆盖建连期  
  - 改法要点：前台 resume 时不仅重建池连接，还需检查 `_LAST_WAKE_AT` 若在 5 分钟内，则主动 re-wake 重建会话。  
  - 预期收益：减少 1006 导致的会话中断。  
  - 风险：中（需防误唤醒）  

---

### 【复核】
- **信号5（auth_failed / 1006）**：✅ **确认**。auth_failed 因 token 空，1006 因后台冻结，均符合预期但可优化。

---

## 三、TTS 维度

### 【诊断】
1. **llm_tts_bad_response 高频**  
   - 日志证据：连续 6 次 `llm_tts_bad_response ct=application/json len=98 intact=False`，内容为同一句「500ml」经典进阶·醇」  
   - 根因分析：`intact=False` 表示响应体截断。百炼 TTS 流式接口在合成固定文本时偶发返回不完整 JSON（如 `{"audio":"...` 缺 `}`），客户端解析失败。重试机制未覆盖此场景（仅 retry on 400ms timeout，非 parse error）。  
   - 置信度：高  

2. **llm_tts_stream_failed: Remote end closed**  
   - 日志证据：`2026-09-25 21:31:04 [WARNING] services.llm_client: llm_tts_stream_failed(回退整句): Remote end closed connection without response`  
   - 根因分析：百炼 TTS 服务端主动断连（可能因负载或超时），客户端未在 stream 读取前校验连接活性。  
   - 置信度：高  

3. **TTS 时延 p50=1305ms 用户感知卡顿**  
   - 日志证据：`tts.total_ms_p50=1305`，用户主诉“不能完整播报”  
   - 根因分析：合成耗时本身高（百炼模型特性），叠加 **无 chunked 播放优化**。当前实现为 fetch 完整句 audio 后才播，而非流式边下边播。  
   - 置信度：高  

---

### 【优化方案】
- **P0**：TTS 响应完整性校验 + 自动重试  
  - 改法要点：在 `services.llm_client` 中，对 TTS 响应 body 做 JSON 完整性检查（如末尾是否 `}`），若 `intact=False` 则立即重试（计入 retry quota）。  
  - 预期收益：bad_response 率降至 <5%。  
  - 风险：低  

- **P0**：实现 TTS 流式播放（chunked playback）  
  - 改法要点：使用 `ReadableStream` + `AudioContext.decodeAudioData` 分片解码播放，首 chunk 到达即播（目标开播 <200ms）。  
  - 预期收益：用户感知延迟下降 50%，解决“不顺畅”投诉。  
  - 风险：中（X5 对 ReadableStream 支持需验证，备选方案：分段 fetch + concat buffer）  

- **P1**：TTS 连接活性预检  
  - 改法要点：stream 开始前 ping 百炼 TTS 健康接口，失败则走整句回退。  
  - 预期收益：减少 `stream_failed`。  
  - 风险：低  

---

### 【复核】
- **信号1（TTS bad_response 30%）**：✅ **确认**。日志显示同一文本连续失败，属百炼服务不稳定 + 客户端无重试。  
- **信号6（TTS 时延导致体验差）**：✅ **确认**。p50=1305ms 远超人耳容忍阈值（<800ms），需流式播放优化。

---

## 四、意图 & 会话维度

### 【诊断】
- **意图轨极度稀疏（turns_total=2）**  
  - 日志证据：`intent.turns_total=2`，`track_llm=0`  
  - 根因分析：因 ASR 大量 ambient noise（23/37），有效指令仅 2 轮，且均命中 rule 轨（`wine.recommend`）。LLM 轨未触发因门控规则生效（无商品语境/非意图动词开头）。  
  - 置信度：高  

- **会话仅 open=1**  
  - 日志证据：`session.open=1`  
  - 根因分析：24h 仅一个有效会话（含 2 轮交互），其余均为无效 ASR 或连接失败。反映 **唤醒成功率极低**（受环境声干扰 + 连接问题）。  
  - 置信度：高  

---

### 【优化方案】
- **P1**：强化免提闲聊门控日志  
  - 改法要点：在 `intent.gatekeeper` 中增加 debug log，记录静默原因（如“no_product_context”）。  
  - 预期收益：便于后续分析 LLM 轨未触发根因。  
  - 风险：无  

（注：意图逻辑本身无 bug，问题源于上游 ASR 质量）

---

### 【复核】
- 无直接对应预诊信号，但 **信号2（环境声）是意图稀疏的主因**，已覆盖。

---

## 五、唤醒维度

### 【诊断】
- **唤醒成功但无 wakeword_in_final**  
  - 已在 ASR 维度分析，属正常现象。  
  - 置信度：中  

- **KWS 轨过度敏感**  
  - 日志证据：环境视频声触发完整 ASR  
  - 根因分析：KWS 使用拼音弱匹配（≤6字），且无二次确认（如 MFCC 相似度），易受电视人声干扰。  
  - 置信度：中  

---

### 【优化方案】
- **P1**：KWS 增加声学特征二次验证  
  - 改法要点：KWS 命中后，提取 MFCC 计算与模板余弦相似度 >0.7 才视为有效唤醒。  
  - 预期收益：降低误唤醒率 30%+。  
  - 风险：中（需维护唤醒词模板库）  

---

## 六、复测建议清单

| 模块 | 验证项 | 方法 |
|------|--------|------|
| **ASR** | KWS 轨不再推送长环境声 | 播放 30s 新闻视频，确认无 ASR 请求或请求被过滤 |
| **ASR** | WebSocket close 后无 send | 模拟 onclose 后触发 send，验证无异常日志 |
| **TTS** | bad_response 自动重试 | Mock 百炼返回截断 JSON，验证重试成功 |
| **TTS** | 流式播放首 chunk <200ms | 使用 Performance API 测量从 TTS start 到 audio play 时间 |
| **连接** | 首次加载 token 容错 | 清除 Cookie 后进入页面，验证自动重试 auth |
| **唤醒** | 误唤醒率下降 | 播放含“小竹”发音的歌曲，统计误唤醒次数（目标 <1/10min） |

---
**总结**：核心问题为 **ASR 成本浪费**（P0）与 **TTS 体验卡顿**（P0），需优先修复。连接层与唤醒层优化为 P1，可显著提升可用性。