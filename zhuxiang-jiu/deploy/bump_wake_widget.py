"""生产 index.html: voice-wake-widget 版本号 bump(幂等)

v=33 -> v=34: G3 遗留定案——流式块 return_sample_rate 动态跟随(24kHz 三重印证); widget VER v=30
v=34 -> v=35: token 预检——beginSegment 开 WS 前检 authToken(),
    登出/换设备 wake 残留时空 token 不再报「识别通道: 鉴权失败」,
    直接提示「请先登录后再唤醒」(省一次 WS 往返)
v=35 -> v=36: 双唤醒词并存——默认匹配器升级「小竹、小竹」两声
    OR「你好小竹」单声任一命中即唤醒(弱音量下两声第一声易被
    ASR 糊掉, 双词兜底); 旧「你好小竹」预设并入默认(存量设置
    自动升级); 提示文案全量同步; widget VER v=30 -> v=31
v=36 -> v=37: 唤醒灵敏度治本——客户端拼音兜底+短文本(≤6字)
    弱匹配(宁误勿漏), auth 上报 wakeword; 服务端唤醒词注入
    百炼 vocabulary 热词(权重 5 偏置修正「猪小猪」类错识) +
    v3 final 转写留痕日志(生产首次部署——唤醒失败轮从此可观测);
    widget VER v=31 -> v=32
v=37 -> v=38: 连接预热 + 意图锚定——V2 预热协议(auth.ver=2 →
    armed → arm 循环多段复用, 唤醒起段省 ~1s 握手, 25s ping 穿
    nginx 空闲超时, 段后保活回池; 服务端双协议旧客户端零影响) +
    商品页上下文锚定(hash→面板预取商品名, 指代词「这个多少钱」
    前缀注入商品名, 文本轮+流式语音轨); widget VER v=32 -> v=33
v=37 -> v=38: 连接预热 + 意图锚定——V2 预热协议(auth.ver=2 →
    armed → arm 循环多段复用, 唤醒起段省 ~1s 握手, 25s ping 穿
    nginx 空闲超时, 段后保活回池; 服务端双协议旧客户端零影响) +
    商品页上下文锚定(hash→面板预取商品名, 指代词「这个多少钱」
    前缀注入商品名, 文本轮+流式语音轨); widget VER v=32 -> v=33
v=38 -> v=39: v2 协议埋点 + arm 超时可见化——15:40 实测零日志
    黑箱(ws_asr_v2_armed/arm 埋点补齐), 客户端 5s arm 超时
    静默拆改提示「识别通道没跟上」; widget VER v=33 -> v=34
v=39 -> v=40: 削顶治本——dump 音频取证勘误「弱音频」误判:
    peak 全钉 32768 满量程削顶(只升不降增益下 X5 AGC 强帧
    撞顶), 「小竹小竹」瞬态被砍致 ASR 只出「我/你」碎片;
    V3.4 双向平滑增益(限速1.6x/帧) + 目标 rms 0.10→0.06 +
    ±0.95 软压缩; dump 开关 XIAOZHU_WS_DUMP(.env)保持常开
    供复测取证; widget VER v=34 -> v=35
v=40 -> v=41: X5 系统 AGC 关闭——17:51 批 dump 实证 V3.4 后
    部分轮仍 peak=32768(「爱。」轮 175122 与 175134):
    源在浏览器采集层即被 AGC 拉到 ±1.0 削顶(方波化, 后端
    软压缩救不了); autoGainControl:false 两处(enableWake/
    enableWakeQuiet), 归一化交给 V3.4 双向增益; widget v=36
v=41 -> v=42: 尖峰压缩收紧——干净重开(AGC off)后 18:13 批
    dump 峰值仍全 32768, X5 无视 autoGainControl 约束实证;
    但 rms 仅 4000~5600=瞬态尖峰(声母爆破音)打满, 非持续
    削顶; V3.5: 压缩参数收紧(0.8 起压×0.15 斜率×0.95 封顶)
    + 增益跨段保留(取消段首重置——重置=1 致段首尖峰未收敛
    即 clamp, 唤醒词声母恰在段首); widget v=37
v=42 -> v=43: V4.5 建连提速——服务端 SSLContext 模块级复用
    (TLS 1.3 session ticket: 每段新连接但握手省 1 RTT, 握手
    凭证复用扬弃连接复用)+建连 3s 快败+段内自动重试一次;
    客户端 armTimer 5→8s 容忍重试(20:46 批 connect_failed
    三连的应对); widget v=38
v=43 -> v=44: V5 停顿双砍——用户主诉「喊完到弹出 4~7s」:
    ①服务端段间预建连接(单 task 用完即弃: 段尾后台 TLS 握手,
    arm 探活 1s 直用——建连移出唤醒关键路径, 隔离性保留);
    ②客户端快收段: 静默判段 1.2s→0.5s(唤醒词 2s 内说完);
    预期端到端停顿降至 ~2s; widget v=39
v=44 -> v=45: 快收段回调——21:12 dump 复盘: 0.5s 快收把
    「小竹-换气-小竹」切成两段致两声匹配必败+噪音间隙段泛滥;
    成功轮段长实证 2.2~4.6s, 收段等待 0.5→0.9s(换气窗口
    0.75s, 比原 1.2s 仍快 0.3s); connect_failed 零出现+
    arm→final 1 秒级(V5 预建生效, 建连层收官); widget v=40
v=45 -> v=46: V5.1 弱轮词头捕获——两声词连续成功(小竹小竹
    ×4)后, 剩余失败轮全部是弱音频轮(rms 456 vs 成功轮 2396+,
    同位置喊能量波动 5~10 倍): ring 1.5→2.5s 回补更早 +
    TH_ON 0.012→0.009 弱轮更早起段——物理边界前的最后一档
    软件压榨; widget v=41
v=46 -> v=47: V5.2 空转写健康轮二打(DAVAS 文档「失败轮无缝
    补救」思想落地)——段音频缓存(与推流逐字节一致)+段源rms
    累计: final 空且源rms≥0.006(健康轮=百炼偶发抖动非音频
    问题)自动同段缓存二打一次(用户无感); 弱轮不重试走提示;
    DAVAS 全貌(Tier探针/降采样/热切换)记为规模化后路线;
    widget v=42
v=47 -> v=48: V5.3 跨段组合——21:44:05「你好。」实锤: 用户喊
    「你好小竹」时中间自然停顿>0.9s 被收段切成「你好」+「小竹」
    两段, 单段全废; 新增呼语挂起(你好|喂|嘿)3s 内紧邻段含
    小竹组词即组合命中; 同修 V5.2 二打竞态(pendingResub 提前
    置空致二打期间 VAD 双 arm); widget v=43
v=48 -> v=49: 待命呼吸动画 + 商品页情境提示——小点呼吸
    (xzPulse 2.4s)示意「监听中」+title 提示语; 商品页停留
    30s 且滚动过半一次性提示「喊小竹小竹说详细介绍」
    (localStorage 防重, 不自动录音仅降门槛); 意图锚定已上线
    (3a83b5d)不重做; barge-in 勘察完毕(面板TTS双轨+speak/
    speakCloud+abort 结构)记单独排期真机验证; widget v=44
v=49 -> v=50: 免唤醒窗口打通——06:54 实证 widget 唤醒命中后
    20 秒面板说话仍被打回「请以小竹开头」(唤醒走 ws_asr 轨
    不写面板会话, 服务端 5 分钟窗口未点亮); widget onWakeHit
    发 xz-panel-wake-hit → 面板置免唤醒态, 发送链自动补
    「小竹，」前缀点亮窗口(text+流式语音轮两处), 用户侧
    无感, 过期后服务端 wakeHint 自动停补; widget v=45
v=50 -> v=51: 唤醒应答主页面直播——X5 面板 iframe 无用户
    手势(唤醒靠 postMessage 开面板), WebAudio ctx 挂起,
    speakCloud 合成成功却静默无声(08:36 实证 163KB 无声);
    「我在，请吩咐」改 widget 主页面 AudioContext 播(开启
    唤醒的点击手势已解锁)——fetch /api/xiaozhu/tts 即取即播;
    面板撤 speak 改 micStatus 文字反馈免双播; widget v=48
v=51 -> v=54(三版累积补 bump——主站引用断链修复: v52~54 只
    bump 了 widget VER 未 bump 此处, 浏览器缓存旧 widget 致
    新面板不可达): v52 selfUtter 12s 时间窗; v53 VAD 断句
    cloudActive 门控+增益底噪回收(长播报回声轰炸根因);
    v54 barge-in 一期; widget v=48 -> v=54
v=54 -> v=55: barge-in 误自打断修复——10:20 实证 TTS 外放
    直达麦克风 rms 0.15+(dump 象限数据), v54 门限 0.12 低于
    此 → 每播必自掐 → cloudStart 乱开段三连建连(102014/15/16
    dump 实证) → 回声碎片段轰炸"不能流程对话"; 门限 0.12→
    0.35 + 基线 1.8x→2.5x + 3 帧→5 帧(80ms 防瞬态); 诚实
    边界: 无 AEC 下等音量/轻声插话能量不可区分, 一期只拦
    显著强插话(B11 用例外放直达 0.15~0.2 持续不自打断);
    widget v=54 -> v=55
v=55 -> v=56: 双轨 barge-in v2(文本确认版, 用户设计"问话
    切断合成——新任务已下达")——能量版(v54/v55)移除: X5 无
    AEC 下外放直达 rms 0.15+ 与人声能量不可分; v2 架构: 段
    提交后 re-arm 连接保活+推流持续 → 播报期间服务端持续
    识别, partial 实时回流 → bargeShouldCut 判定(非回声
    ≥3 字或「小竹」开头 ≥2 字=新任务)→ 切断合成+finish 收
    段等完整 final 提交(不吞半句); 回声由 bargeIsEcho 拦
    (lastSpoken 宽松子串无长度上限+selfUtter 集合 12s 窗
    全遍历); vtick 段首清 streamPartial 防回声残留污染
    VAD 尾字; widget v=55 -> v=56
v=56 -> v=57: 双轨回声判定升级切句+子序列——11:05:55 实证
    子串失效: 外放"竹奕·竹香尊享 24690 元"被 ASR 丢字识回
    "竹奕，24690", 不再是原文子串; 混合拼接段"竹奕24690。我
    在请吩咐"(回声+widget 应答)整段永非单一子串 → 误判新指
    令 → 误切断+提交 → 小竹自言自语"自己强说"。修复: ①按
    句读拆分逐句判, 完整句(≥3字)皆命中才是回声(混合段全拦);
    ②子序列判定(字符按序出现即命中——丢字回声"竹奕24690"
    是"竹奕竹香尊享24690"的按序子序列); 保守方向: 宁漏切断
    (播报继续续听补位)不误切断; 真指令带播报外新词必放行;
    widget v=56 -> v=57
v=57 -> v=58: 回声判定三级防御(语义-时序联合校验文档借鉴)——
    子序列对插字/同音替换仍失效("竹奕啊24690"语气词断链/
    "逐奕24690"竹→逐); ①norm 去语气词(啊呢吧嘛哦嗯呀啦);
    ②LCS 比率 ≥0.75(ASR 句长分母)替代子序列——丢字/插字/
    同音三失效模式一个判定全覆盖; ③纠正元词强制放行
    (不对|不是|别说了|闭嘴|停|等等无视相似度切断——纠错
    强意图; 不含取消/继续类操作词, 引导语播报"您可以说
    取消"会误触); 能量灰色带仲裁(第三级)记为规模化后路线;
    widget v=57 -> v=58
v=58 -> v=59: 数字形态归一(11:16 误切断根因)——TTS 文本
    "42度"被外放后 ASR 识回"四十二度", 中文数字 vs 阿拉伯
    LCS 对不上(比率骤降<0.75)→回声判定失效→播报被误切+
    finish 空提交失败无应答("不能完整播报"); 文档归一化清单
    之数字统一上轮漏做; cn2num 中文数字段转阿拉伯(四十二→
    42/两万四千六百九十→24690, 万级简化), norm 内对 ASR
    句与 lastSpoken 双侧统一; widget v=58 -> v=59
v=59 -> v=60: 二级对话唤醒窗口制(用户方案"不问不答, 问即
    唤醒, 答完即退")——免提从"全时监听+软件过滤"(回声轰炸/
    自言自语/误切断全在这条链上打补丁)转为"按需窗口": ①8s
    聆听窗口无话 → listenClose 轻关闭静默回归一级(保留已
    授权流防重取 getUserMedia 丢字首, 拆 WS 停 VAD 判定,
    音频无消费者=功能等价关闭拾音); ②回声/环境声从源头
    不进系统, 自言自语/误切断链路整体退役(v56 段后 re-arm
    双轨供能点移除——播报期拾音关闭, 播报完整性恢复);
    ③打断走唤醒词: widget KWS 独立流常开, 播报中喊「小竹
    小竹」→ wake-hit → cloudStart(内置 stopTtsNow 打断)+
    重开窗; ④多轮对话=窗口接力(每轮播报完 done→schedule
    Handfree 开新窗, 窗口内说话免唤醒直达); 双轨 barge 判定链保留为死代码防御层(留给未来 AEC 方案); widget v=59
    -> v=60
v=60 -> v=61: 唤醒应答回环双窗修复(11:28 实证"自己说+回答
    不了"完整病理)——wake-hit 开窗与 widget「我在，请吩咐」
    直播同时, 应答外放直达麦克风 rms 超任何阈值被 VAD 判
    "说话"→vadSpoke=true 窗口不关→尾音静音断句提交恰在
    wakeGrace 4s 边缘外→回声轮连环(4 连短 TTS)→回声轮
    not_woken 打回重置 woken→用户真话「介绍一款52度的酒」
    无前缀被打回"请以小竹开头"。修复: 双窗分离——①wakeVad
    Mute 2.5s(VAD 静默窗: 应答播放期 vtick 不判定不置
    vadSpoke; 不吞用户指令头——唤醒→说话节奏 >2s); ②wake
    Grace 4s→4.5s(提交丢弃窗防尾音段边缘提交); widget v=60
    -> v=61
v=61 -> v=62: 应答播放状态跨页同步(11:35 实证 2.5s 静默窗
    盲区)——widget「我在，请吩咐」开播延迟 0.5~3s(fetch+
    decode), 静默窗常在开播前过期→应答声进 VAD→回声段
    「我在，请吩咐。」(10s 混环境底噪)提交→"这个我还在学
    着呢"兜底轰炸自言自语; 治本: 不再猜时间窗——widget 播放
    start/end postMessage 面板(xz-wake-audio-start/end),
    面板 VAD 静默精确跟随实际播放(start→Infinity, end→
    now+0.8s 尾音); end 超时兜底 5s(onended 丢失防 Infinity
    卡死); TTS 失败路径由 wake-hit 2.5s 起始窗兜底; widget
    v=61 -> v=62
v=62 -> v=63: 静默等待式 S1 纯声学就绪(四态状态机文档落地)
    ——「我在，请吩咐」语音应答彻底取消: 四轮回环(11:05/11:
    16/11:28/11:35)全由它的外放回声触发, 语音内容必可被
    ASR 识别成指令(播放同步只是治标); widget 只播纯声学
    「叮-咚」就绪音(WebAudio 合成 0.4s 零网络零语义——文
    档"S1 提示音必须纯声学"), 面板 micStatus 文字反馈"听
    令中"; v62 播放同步机制(xz-wake-audio-start/end)随语音
    应答退役删除; wakeVadMute 2.5s→1s(只盖 beep 尾音——用
    户 1s 后即可说话, 无应答等待); S3 续问窗 8s→5s(文档值,
    环境声暴露面收窄); 句首保护: streamFeed 建连期帧攒入
    stAccum ready 后补推(文档"首字保护"——"叮咚"后立即
    开口的头 0.5~1s 不再丢字); widget v=62 -> v=63
"""
import io
import os

INDEX = os.environ.get("WAKE_INDEX", "/var/www/zxjiu/dist/index.html")
OLD = "src=/js/voice-wake-widget.js?v=62"
NEW = "src=/js/voice-wake-widget.js?v=63"


def main():
    s = io.open(INDEX, encoding="utf-8").read()
    if NEW in s:
        print("already-bumped")
        return
    old_ver = OLD.rsplit("=", 1)[-1]
    new_ver = NEW.rsplit("=", 1)[-1]
    assert OLD in s, f"anchor {old_ver} not found"
    io.open(INDEX, "w", encoding="utf-8").write(s.replace(OLD, NEW, 1))
    print(f"bumped {old_ver} -> {new_ver}")


if __name__ == "__main__":
    main()
