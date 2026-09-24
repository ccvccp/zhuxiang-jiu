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
"""
import io
import os

INDEX = os.environ.get("WAKE_INDEX", "/var/www/zxjiu/dist/index.html")
OLD = "src=/js/voice-wake-widget.js?v=46"
NEW = "src=/js/voice-wake-widget.js?v=47"


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
