"""50号·小竹语音信值积分引擎 P1 声纹验证器(双态)

计划(docs/50号_小竹语音信值积分引擎实施计划.md §三-3/§七 P1):
    双态交付(红线裁定):
    - proxy(默认): 确定性哈希代理——会员绑定信值档案+
      语音通道 → verified(半程加成 ×1.25; 49号 P1 代理
      不作凭证口径); 未绑定/文本通道 → unverified(×0.3)
    - real(国标 SDK 集成后翻转, 外部待办): 活体检测模拟
      (确定性 liveness)→ 全额 ×1.5; TTS 疑似频谱分析
      留 P4 反作弊闸门(字段先留)

审计标注: 验证结果 mode/verified 落 voice50 事件
voiceprintMode 字段(P0 已建)——管理端可溯每笔积分
的声纹依据。

红线:
    - proxy 加成只入台账(T+1 桥接轨道 P2 硬编码拒绝
      proxy 来源的 L2/L3 加成——L1 本就不桥接)
    - 验证器纯确定性(mock 声谱特征——不引入随机性)
"""

import logging
import os

logger = logging.getLogger("xiaozhu_voice50_vp")

# 双态开关(VOICE50_VOICEPRINT_MODE=proxy|real)
VP_MODE_DEFAULT = "proxy"
VP_MODE_REAL = "real"
VP_MODE_PROXY = "proxy"

# 活体模拟分(确定性——real 态)
LIVENESS_SIMULATED = 0.9

# TTS 疑似阈值(P4 反作弊闸门消费; 字段先留)
TTS_SUSPECT_THRESHOLD = 0.5


def voiceprint_mode() -> str:
    """声纹验证模式(proxy 默认/real 外部待办)"""
    m = os.environ.get("VOICE50_VOICEPRINT_MODE",
                       VP_MODE_DEFAULT).lower()
    return m if m in (VP_MODE_PROXY, VP_MODE_REAL) \
        else VP_MODE_DEFAULT


def speaker_proxy_digest(member_id: int) -> str:
    """声纹代理摘要(49号 executor 同款——确定性哈希,
    不作身份凭证; 跨用户复用校验口径一致)"""
    import hashlib
    return hashlib.sha256(
        f"speaker:{member_id}".encode(
            "utf-8")).hexdigest()[:32]


def liveness_score(member_id: int,
                   session_id: int) -> float:
    """活体检测模拟(确定性——真 SDK 前的联调占位)

    确定性口径: 会话哈希映射 [0.85, 0.95]——同会话
    稳定复现(测试幂等); TTS 疑似(<0.5)由 P4 闸门显式
    注入, 此处不产生。
    """
    import hashlib
    raw = f"liveness:{member_id}:{session_id}"
    h = int(hashlib.sha256(
        raw.encode("utf-8")).hexdigest()[:8], 16)
    return round(0.85 + (h % 100) / 1000.0, 3)


async def verify(member_id: int, session: dict,
                 channel: str,
                 binding_repo=None) -> dict:
    """声纹验证(P1 真实现——钩子内调用)

    Args:
        binding_repo: 48号仓储(绑定检查; 缺省时
            自建——与 49号 get_binding 同口径)
    Returns:
        {mode, verified, liveness, speakerDigest,
         multiplier, note}
        multiplier: 声纹系数(real 1.5 / proxy 1.25 /
        未验证 0.3——系数语义与引擎 P0 一致)
    """
    mode = voiceprint_mode()
    if binding_repo is None:
        from repositories.xiaozhu_repository import (
            Xiaozhu48Repository,
        )
        binding_repo = Xiaozhu48Repository()
    binding = await binding_repo.get_binding(member_id)
    bound = binding is not None
    digest = speaker_proxy_digest(member_id)
    liveness = liveness_score(
        member_id, (session or {}).get("sessionId") or 0)

    # 语音通道+绑定 → 验证通过(双态加成)
    if channel == "voice" and bound:
        if mode == VP_MODE_REAL:
            return {
                "mode": VP_MODE_REAL, "verified": True,
                "liveness": liveness,
                "speakerDigest": digest,
                "multiplier": 1.5,
                "note": "real 模式(活体模拟 "
                        f"liveness={liveness})",
            }
        return {
            "mode": VP_MODE_PROXY, "verified": True,
            "liveness": liveness,
            "speakerDigest": digest,
            "multiplier": 1.25,
            "note": "proxy 代理验证(绑定+语音; "
                    "不作凭证——加成只入台账)",
        }
    # 文本通道/未绑定 → 未验证
    reason = ("文本通道非声纹"
              if channel != "voice" else "未绑定信值档案")
    return {
        "mode": mode, "verified": False,
        "liveness": 0.0, "speakerDigest": digest,
        "multiplier": 0.3,
        "note": f"未验证({reason})——×0.3",
    }
