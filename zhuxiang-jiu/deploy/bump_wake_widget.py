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
"""
import io
import os

INDEX = os.environ.get("WAKE_INDEX", "/var/www/zxjiu/dist/index.html")
OLD = "src=/js/voice-wake-widget.js?v=37"
NEW = "src=/js/voice-wake-widget.js?v=38"


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
