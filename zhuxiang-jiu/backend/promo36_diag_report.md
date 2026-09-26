# 36号 AI 智能推广联合检测报告(千问 qwen3-max, 2026-09-26 生产取证)

# 36号AI智能推广模块联合检测报告

## 1. 【总裁定】  
**36号未真正正常工作，未实现任何真实推广效果。当前仅为全链路mock空转系统，唯一真实动作（百度sitemap推送）已被robots规则压制，对外触达与转化均为零。**

---

## 2. 【证据链复核】

| 预诊条目 | 复核结论 | 依据与修正 |
|--------|--------|----------|
| **1. 空转定性** | ✅ **确认** | - 调度器日志显示 promo_radar_scan 扫描25条但 new=0、skipped=25（因48h指纹去重窗内全为mock池重复）<br>- published内容回执明确标注 `"mode": "mock"`，exposureEstimate=1,483,500为固定模拟值<br>- 唯一真实通道BAIDU_PUSH虽调用10次，但推送URL `/r/A-xxx` 被同日P0修复的 `robots.txt Disallow: /r/` 阻断，实际无法被收录 |
| **2. 名实问题** | ✅ **确认** | - agentTrace 全部四步（分析/匹配/生成/自查）均 = `"rule"`，证明GLM Agent从未启用<br>- 内容标题高度重复（如多篇“中秋团圆宴白酒清单”），合规分全100且含健康警示——典型规则模板特征<br>- PROMO_CHANNEL_MODE=mock + HOTSPOT_*_API_KEY全空 → 无真实热点输入，LLM无触发条件 |
| **3. 冲突误伤** | ✅ **确认，需紧急修正** | - 百度推送URL格式为 `/r/A-xxx`，而robots.txt新增 `Disallow: /r/` 直接禁止所有爬虫抓取该路径<br>- attract点击数据显示99.88%为爬虫（PetalBot等），referer=sitemap，说明短链曾被sitemap索引但现已被拒抓<br>- **冲突本质**：SEO主动推送（需Baiduspider抓取）与防刷点击策略（全局禁/r/）目标冲突 |
| **4. 数据风险** | ✅ **确认，且风险升级** | - evo_log=11、evo_bandit=1、cat_stats/style_stats已生成 → **Bandit算法在零真实reward下基于mock曝光（148万）更新权重**<br>- 若此权重用于未来真实决策，将导致品类偏好严重偏移（如过度倾向“中秋”“露营”等mock主题）<br>- **遗漏盲区**：mock exposureEstimate若流入report_platform报表，将虚增GMV/点击指标，误导运营判断 |
| **5. 链路成熟度** | ✅ **确认，补充关键细节** | - 链路代码完整，但**所有外部依赖均未激活**：<br>  • 热点源：HOTSPOT_*_API_KEY全空 → mock池<br>  • 发布凭证：PROMO_CHANNEL_MODE=mock → 无平台KEY调用<br>  • LLM：无GLM API KEY配置 → 四步链强制降级至rule<br>  • 流量：attract点击≈2真人，converted=0 → 无真实反馈闭环 |

---

## 3. 【空转地图】

| 环节 | 状态 | 证据 |
|------|------|------|
| **雷达** | mock空转 | HOTSPOT_*_API_KEY全空；scanned=25/new=0/skipped=25（mock池+48h去重） |
| **决策** | mock空转 | 输入为mock热点；auto_engage=115条但基于固定池评分；无真实热度/速度数据 |
| **内容生成** | mock空转（规则模板） | agentTrace全="rule"；标题重复；无GLM调用日志；合规分100（模板硬编码） |
| **发布** | mock空转 | 回执mode="mock"；exposureEstimate=1,483,500为模拟值；五平台KEY未启用real模式 |
| **回流（效果）** | **部分真实但无效** | BAIDU_PUSH调用10次（真实动作），但推送URL被robots.txt禁止抓取 → **实际无收录/无点击**；attract点击≈2真人，converted=0 |
| **进化层** | **危险运转（mock污染）** | evo_bandit=1；cat_stats/style_stats已更新 → **基于虚假曝光学习，污染未来决策** |

---

## 4. 【修复优先日晚间】

### P0（立即执行，24h内）
- **(a) robots/百度推送冲突最优解**：  
  **修改robots.txt，对Baiduspider放行/r/路径**：  
  ```txt
  User-agent: Baiduspider
  Allow: /r/
  
  User-agent: *
  Disallow: /r/
  ```  
  **预期收益**：恢复百度SEO收录能力，使A-xxx短链可被索引 → 获取真实搜索流量  
  **风险**：需确保/r/路径有反爬机制（如频控、UA校验），防止非Baiduspider爬虫刷点击  

- **(b) 进化层mock污染止损**：  
  **立即清空evo_bandit/evo_log/cat_stats/style_stats，并暂停PROMO_EVOLUTION_AUTO直至真实流量>100点击/日**  
  **预期收益**：避免Bandit权重被mock数据污染，保障未来决策准确性  
  **风险**：无；进化层本应依赖真实reward，当前零转化下学习无意义  

### P1（3日内）
- **启用至少1个真实热点源（建议百度热榜）**：  
  配置HOTSPOT_BAIDU_API_KEY/URL → 获取真实热点 → 触发LLM四步链  
  **预期收益**：打破mock循环，生成真实场景内容；提升内容时效性与相关性  
  **放弃建议**：小红书/知乎热榜可暂缓（流量权重低），优先保障百度+抖音  

- **启用GLM Agent（至少glm-4-flash）**：  
  配置GLM_API_KEY → 允许step3Generate走AI生成而非rule模板  
  **预期收益**：内容多样性提升，避免标题重复；增强受众匹配精度  
  **替代方案**：若LLM成本高，可保留rule模板但**仅用于pass档内容**，auto_engage必须走AI  

### P2（1周内）
- **灰度开启1个真实发布通道（建议微博）**：  
  将PROMO_CHANNEL_MODE从mock切换为real（仅weibo）→ 验证发布回执真实性  
  **预期收益**：获取真实曝光/互动数据，验证归因短码有效性  
  **风险控制**：日上限设为5条，人工review强制开启  

---

## 5. 【效果预期：可观测验收判据】

按上述P0修复后，**以下任一信号出现即标志“36号真正开始工作”**：

1. **真实热点占比 > 30%**：redis中热点来源字段含 `source: baidu_real` 或 `douyin_real`，且非mock池标题  
2. **LLM轨迹启用率 > 50%**：agentTrace中至少step3Generate或step1Analysis出现 `"glm-4"` 或 `"glm-5"` 标记  
3. **真实点击 > 0 且持续**：attract日志中非爬虫点击（UA非bot、停留>10s）连续2天 ≥ 5次  
4. **百度收录验证**：site:yourdomain.com inurl:/r/A-xxx 返回结果 ≥ 1（证明robots放行生效）  
5. **转化漏斗启动**：registered ≥ 1 或 converted ≥ 1（哪怕仅1单）

> **关键原则**：**拒绝任何mock数据进入效果报表与进化层**。真实推广的起点是“真实输入 → 真实输出 → 真实反馈”闭环建立，而非模拟数字的自我循环。