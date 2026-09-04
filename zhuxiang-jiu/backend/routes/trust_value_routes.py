"""45号·信值模块路由(P0 角色档案 + 三层评分)

端点(P0, 自助 3 + 管理 1):
    POST /api/trust/roles                自助建档(证件明文仅本次
                                         使用, 落盘 SHA-256 摘要)
    GET  /api/trust/roles/{trustId}      档案视图(分层明细/熔断态/
                                         最近事件/摘要脱敏)
    POST /api/trust/roles/{trustId}/score 触发重算(熔断判定→
                                         三层评分→锁档→落盘)
    POST /api/trust/roles/{trustId}/events 行为事件灌入(P0 数据
                                         通道——管理端 manual;
                                         P1 起雷达/存证接管)

端点(P1, AI 雷达三通道 5):
    POST /api/trust/radar/scan           公开域一轮扫描(白名单源
                                         +去标识化+验真入分)
    POST /api/trust/probes               授权登记(显式授权留痕+
                                         mock 确定性读数入分)
    GET  /api/trust/probes/{trustId}     角色的授权列表(留痕事件)
    POST /api/trust/deposits             自愿存证上传(验真三道关+
                                         因果净贡献折算)
    GET  /api/trust/deposits/{id}/status 存证验真状态查询

端点(P2, 即时修复引擎 4):
    POST /api/trust/repairs              提交修复证据包(验真→
                                         修复值→天花板→入分)
    GET  /api/trust/repairs/{trustId}/plan 修复建议路径(违规即列,
                                         β 加权最优清单+时效窗口)
    GET  /api/trust/repairs/detail/{id}  修复明细(归因回放)
    POST /api/trust/repairs/{id}/verify  触发验真(验真明细回放)

端点(P3, 信值资产与价值兑换 5):
    GET  /api/trust/balance              余额+冻结+准备金池+上限
    POST /api/trust/redeem               兑换申请(1TV=1元货品,
                                         防挤兑四件套校验)
    POST /api/trust/redeem/{id}/confirm  商户核销(TV 实时销毁)
    POST /api/trust/convert              信用分→TV 单向转换
    GET  /api/trust/ledger               账本流水(只追加不可篡改)

端点(P4, 自进化闭环 7):
    POST /api/trust/appeals              提交申诉(信值变动异议,
                                         7 日窗口)
    GET  /api/trust/appeals              申诉队列(管理端)
    POST /api/trust/appeals/{id}/decide  人工复核(upheld 计算
                                         正确/overturned 翻转
                                         重算)
    GET  /api/trust/attribution/{tid}/{eid} 归因报告(可解释性
                                         强制, LLM 三态)
    POST /api/trust/learning/collect     裁决真值批量回流
    POST /api/trust/learning/run          触发一轮 Hedge 学习
    GET  /api/trust/learning/status       学习状态视图
    POST /api/trust/patches               伦理补丁注入(β 映射
                                         更新, 版本留痕)
    GET  /api/trust/patches               补丁历史(版本审计)

端点(P5, 对外服务开放面 6——走 44号 Key 网关):
    GET  /api/trust/open/query/{trustId}       ①信值查询
                                              (字段级脱敏+修复建议)
    POST /api/trust/open/redeem/confirm       ②兑换核销(幂等键
                                              +nonce 防重放)
    POST /api/trust/open/deposits             ③行为存证(敏感词
                                              过滤+验真)
    POST /api/trust/open/convert              ④信用分单向转换
                                              (nonce 防重放)
    GET  /api/trust/open/audit/{trustId}      ⑤监管审计(只读,
                                              全留痕)
    GET  /api/trust/open/dashboard            治理看板聚合
                                              (六区块数据源)

鉴权:
    - 自助面(建档/查询/重算/存证/修复/兑换/转换/申诉): 公开
      (信值查询脱敏口径——摘要掩码展示, 明文永不返回)
    - 事件灌入/申诉裁决/学习三连/补丁: X-Role: admin
      (43/44号同款口径)
    - 开放面(/open/*): 44号 Key 网关(台账 published 后
      Key 双头+QPS+日配额; 未发布时公开兜底, 业务层
      防重放/幂等/审计先行)

统一口径:
    - 模块纯增量(零既有路由改动)
    - KeyError → 404 / ValueError → 409(44号同款)
    - 价值红线: TV 只兑货品/服务, 不可兑现金/不可二级交易
      (账本 direction 枚举锁死, 无 transfer_out 类型;
      开放面无任何转账类 API)
"""

from fastapi import APIRouter, Header, HTTPException, Query

from services.trust_scoring_service import TrustProfileService

router = APIRouter(prefix="/api/trust",
                   tags=["信值模块(45号)"])
_service = TrustProfileService()


def _require_admin(x_role: str | None):
    if x_role != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限")


def _handle(exc: Exception):
    """统一异常映射(43/44号同款)"""
    if isinstance(exc, KeyError):
        msg = str(exc) if str(exc) else "资源不存在"
        if msg.startswith("'") and msg.endswith("'"):
            msg = msg[1:-1]
        raise HTTPException(status_code=404, detail=msg)
    if isinstance(exc, ValueError):
        raise HTTPException(status_code=409, detail=str(exc))
    raise HTTPException(status_code=500, detail=str(exc))


@router.post("/roles")
async def create_role(body: dict):
    """自助建档(双角色: person 个人 / org 企业机构)

    body: {role, name, idNumber}——证件号仅本次使用,
    存储只留 SHA-256 摘要(防重复建档唯一键)。
    """
    if not isinstance(body, dict):
        raise HTTPException(status_code=409, detail="请求体需为对象")
    try:
        return await _service.create_role(
            str(body.get("role") or ""),
            str(body.get("name") or ""),
            str(body.get("idNumber") or ""))
    except Exception as e:
        raise _handle(e) from e


@router.get("/roles/{trust_id}")
async def get_role(trust_id: int):
    """档案视图(分层明细 + 熔断态 + 最近事件)"""
    try:
        return await _service.get_profile(trust_id)
    except Exception as e:
        raise _handle(e) from e


@router.post("/roles/{trust_id}/score")
async def rescore_role(trust_id: int):
    """触发重算(熔断判定 → 三层评分 → 锁档 → 落盘)"""
    try:
        return await _service.compute_score(trust_id)
    except Exception as e:
        raise _handle(e) from e


@router.post("/roles/{trust_id}/events")
async def record_role_event(
    trust_id: int,
    body: dict,
    x_role: str = Header(default="", alias="X-Role"),
):
    """行为事件灌入(P0 管理端数据通道)

    body: {layer: L1|L2|L3, factor, delta ∈ [-100,100],
           severity?: general|severe|criminal(仅 L1 语义),
           summary?, consistency?(P6 UEBA L2 伪善预警 0-1),
           selfPromotion?(P6 UEBA L3 作秀降权 0-1)}
    P1 起由 AI 雷达/授权探针/自愿存证以 source 接管, 本端点
    保留为 manual 通道。
    """
    _require_admin(x_role)
    if not isinstance(body, dict):
        raise HTTPException(status_code=409, detail="请求体需为对象")
    try:
        return await _service.record_event(
            trust_id,
            layer=str(body.get("layer") or ""),
            factor=str(body.get("factor") or ""),
            delta=body.get("delta") or 0,
            severity=str(body.get("severity") or "general"),
            source="manual",
            summary=str(body.get("summary") or ""),
            consistency=body.get("consistency"),
            self_promotion=body.get("selfPromotion"))
    except Exception as e:
        raise _handle(e) from e


# ============================================================
# P1: AI 雷达三通道(公开域扫描 / 授权探针 / 自愿存证)
# ============================================================

@router.post("/radar/scan/{trust_id}")
async def radar_scan(trust_id: int):
    """公开域一轮扫描(白名单源定向 + 去标识化 + 验真入分)

    mock 态确定性扫描(同档案结果恒定); real 态外接检索
    API 凭证待办。公开域数据天然多源权威——跨源关直判。
    """
    try:
        from services.trust_radar_service import TrustRadarService
        return await TrustRadarService().scan_public(trust_id)
    except Exception as e:
        raise _handle(e) from e


@router.post("/probes")
async def register_probe(body: dict):
    """授权登记(显式授权留痕; 严禁爬虫抓取私有域)

    body: {trustId, provider: zhima|platform_credit|
    bank_reference, scope?}——登记后 mock 确定性读数入分。
    """
    if not isinstance(body, dict):
        raise HTTPException(status_code=409, detail="请求体需为对象")
    try:
        from services.trust_radar_service import TrustRadarService
        return await TrustRadarService().register_probe(
            int(body.get("trustId") or 0),
            str(body.get("provider") or ""),
            str(body.get("scope") or "credit_score"))
    except (TypeError, ValueError) as e:
        if "int()" in str(e):
            raise HTTPException(
                status_code=409,
                detail="trustId 需为整数") from e
        raise _handle(e) from e
    except Exception as e:
        raise _handle(e) from e


@router.get("/probes/{trust_id}")
async def list_probes(trust_id: int):
    """角色的授权列表(授权留痕事件)"""
    try:
        from services.trust_radar_service import TrustRadarService
        return await TrustRadarService().list_probes(trust_id)
    except Exception as e:
        raise _handle(e) from e


@router.post("/deposits")
async def submit_deposit(body: dict):
    """自愿存证上传(AI 先验真 → 因果净贡献折算 → 入库)

    body: {trustId, layer, factor, observed(申报绝对量),
    peerBaseline(同类角色群体基线), evidence(证据内容),
    summary?, sources?,
    voluntary?(P6 UEBA 自愿披露激励——正向 delta ×1.05)}
    ——验真不过不入分(孤证/置信度不足)。
    """
    if not isinstance(body, dict):
        raise HTTPException(status_code=409, detail="请求体需为对象")
    try:
        from services.trust_radar_service import TrustRadarService
        return await TrustRadarService().submit_deposit(
            int(body.get("trustId") or 0),
            layer=str(body.get("layer") or ""),
            factor=str(body.get("factor") or ""),
            observed=body.get("observed") or 0,
            peer_baseline=body.get("peerBaseline") or 0,
            evidence=str(body.get("evidence") or ""),
            summary=str(body.get("summary") or ""),
            sources=body.get("sources"),
            voluntary=body.get("voluntary"))
    except (TypeError, ValueError) as e:
        raise _handle(e) from e
    except Exception as e:
        raise _handle(e) from e


@router.get("/deposits/{deposit_id}/status")
async def deposit_status(deposit_id: int):
    """存证验真状态查询(applied / rejected)"""
    try:
        from services.trust_radar_service import TrustRadarService
        return await TrustRadarService().deposit_status(
            deposit_id)
    except Exception as e:
        raise _handle(e) from e


# ============================================================
# P2: 即时修复引擎("立地成佛"——违规确认即开通道)
# ============================================================

@router.post("/repairs")
async def submit_repair(body: dict):
    """提交修复证据包(验真 → 修复值 α×ΣβVγ → 天花板 → 入分)

    body: {trustId, violationEventId, repairs: [{kind,
    value(1-100), evidence, daysSince?}], sources?}
    ——24h 内修复效率约为 30 天后的 18 倍(高效激励窗口)。
    """
    if not isinstance(body, dict):
        raise HTTPException(status_code=409, detail="请求体需为对象")
    try:
        from services.trust_repair_service import (
            TrustRepairService,
        )
        return await TrustRepairService().submit_repair(
            int(body.get("trustId") or 0),
            int(body.get("violationEventId") or 0),
            body.get("repairs") or [],
            sources=body.get("sources"))
    except (TypeError, ValueError) as e:
        raise _handle(e) from e
    except Exception as e:
        raise _handle(e) from e


@router.get("/repairs/{trust_id}/plan")
async def repair_plan(trust_id: int):
    """修复建议路径(违规即列, 无等待期——β 加权最优清单)"""
    try:
        from services.trust_repair_service import (
            TrustRepairService,
        )
        return await TrustRepairService().repair_plan(trust_id)
    except Exception as e:
        raise _handle(e) from e


@router.get("/repairs/detail/{repair_id}")
async def repair_detail(repair_id: int):
    """修复明细(归因回放)"""
    try:
        from services.trust_repair_service import (
            TrustRepairService,
        )
        return await TrustRepairService().repair_detail(
            repair_id)
    except Exception as e:
        raise _handle(e) from e


@router.post("/repairs/{repair_id}/verify")
async def repair_verify(repair_id: int):
    """触发验真(验真明细回放——提交时已同步完成三道关)"""
    try:
        from services.trust_repair_service import (
            TrustRepairService,
        )
        return await TrustRepairService().trigger_verify(
            repair_id)
    except Exception as e:
        raise _handle(e) from e


# ============================================================
# P3: 信值资产与价值兑换(1 TV = 1 元)
# ============================================================

@router.get("/balance/{trust_id}")
async def trust_balance(trust_id: int):
    """余额视图(可用/冻结/发行统计/准备金池/兑换上限)"""
    try:
        from services.trust_asset_service import (
            TrustAssetService,
        )
        return await TrustAssetService().balance(trust_id)
    except Exception as e:
        raise _handle(e) from e


@router.post("/redeem")
async def trust_redeem(body: dict):
    """兑换申请(1 TV = 1 元货品/服务; 防挤兑四件套)

    body: {trustId, amount, merchant, goods?}——
    校验链: 熔断冻结→可用余额→日/月上限→商户保证金→
    pending(额度锁定), 商户核销后 TV 销毁。
    """
    if not isinstance(body, dict):
        raise HTTPException(status_code=409, detail="请求体需为对象")
    try:
        from services.trust_asset_service import (
            TrustAssetService,
        )
        return await TrustAssetService().redeem(
            int(body.get("trustId") or 0),
            body.get("amount") or 0,
            str(body.get("merchant") or ""),
            str(body.get("goods") or ""))
    except (TypeError, ValueError) as e:
        raise _handle(e) from e
    except Exception as e:
        raise _handle(e) from e


@router.post("/redeem/{redeem_id}/confirm")
async def trust_redeem_confirm(
    redeem_id: int,
    body: dict,
):
    """商户核销确认(TV 实时销毁 + 行为资产标记已消耗)

    body: {merchant}——仅申请商户本人可核销。
    """
    if not isinstance(body, dict):
        raise HTTPException(status_code=409, detail="请求体需为对象")
    try:
        from services.trust_asset_service import (
            TrustAssetService,
        )
        return await TrustAssetService().redeem_confirm(
            redeem_id, str(body.get("merchant") or ""))
    except (TypeError, ValueError) as e:
        raise _handle(e) from e
    except Exception as e:
        raise _handle(e) from e


@router.post("/convert")
async def trust_convert(body: dict):
    """信用分 → TV 单向转换(动态汇率, 转换后信用分同步扣减)

    body: {trustId, userId, creditPoints}——TV → 信用分
    方向永久禁止(防套利循环)。
    """
    if not isinstance(body, dict):
        raise HTTPException(status_code=409, detail="请求体需为对象")
    try:
        from services.trust_asset_service import (
            TrustAssetService,
        )
        return await TrustAssetService().convert(
            int(body.get("trustId") or 0),
            int(body.get("userId") or 0),
            body.get("creditPoints") or 0)
    except (TypeError, ValueError) as e:
        raise _handle(e) from e
    except Exception as e:
        raise _handle(e) from e


@router.get("/ledger/{trust_id}")
async def trust_ledger(trust_id: int,
                       limit: int = 50):
    """账本流水(只追加不可篡改; issue/burn/transfer_in)"""
    try:
        from services.trust_asset_service import (
            TrustAssetService,
        )
        return await TrustAssetService().ledger(trust_id,
                                                limit=limit)
    except Exception as e:
        raise _handle(e) from e


@router.post("/merchant/deposit")
async def merchant_deposit(body: dict,
                           x_role: str = Header(default="",
                                                 alias="X-Role")):
    """商户缴纳保证金(管理动作——兑换履约担保)"""
    _require_admin(x_role)
    if not isinstance(body, dict):
        raise HTTPException(status_code=409, detail="请求体需为对象")
    try:
        from services.trust_asset_service import (
            TrustAssetService,
        )
        return await TrustAssetService().merchant_deposit_add(
            str(body.get("merchant") or ""),
            body.get("amount") or 0)
    except (TypeError, ValueError) as e:
        raise _handle(e) from e
    except Exception as e:
        raise _handle(e) from e


# ============================================================
# P4: 自进化闭环(申诉复核 + 裁决回流 + 归因 + 伦理补丁)
# ============================================================

@router.post("/appeals")
async def submit_appeal(body: dict):
    """提交申诉(信值变动异议; 7 日窗口)

    body: {trustId, eventId, reason}
    """
    if not isinstance(body, dict):
        raise HTTPException(status_code=409, detail="请求体需为对象")
    try:
        from services.trust_learning_service import (
            TrustAppealService,
        )
        return await TrustAppealService().submit_appeal(
            int(body.get("trustId") or 0),
            int(body.get("eventId") or 0),
            str(body.get("reason") or ""))
    except (TypeError, ValueError) as e:
        raise _handle(e) from e
    except Exception as e:
        raise _handle(e) from e


@router.get("/appeals")
async def list_appeals(
    status: str = Query(None, description="状态过滤"
                                    "(pending/upheld/overturned)"),
    x_role: str = Header(default="", alias="X-Role"),
):
    """申诉队列(管理端)"""
    _require_admin(x_role)
    try:
        from services.trust_learning_service import (
            TrustAppealService,
        )
        return await TrustAppealService().list_appeals(
            status=status)
    except Exception as e:
        raise _handle(e) from e


@router.post("/appeals/{appeal_id}/decide")
async def decide_appeal(appeal_id: int, body: dict,
                        x_role: str = Header(default="",
                                             alias="X-Role")):
    """人工复核裁决(计算正确性真值源)

    body: {uphold: bool, note?}——upheld=计算正确(正反馈);
    overturned=计算错误(反向事件+熔断计数回退+重算)。
    """
    _require_admin(x_role)
    if not isinstance(body, dict) or \
            "uphold" not in body:
        raise HTTPException(status_code=409,
                            detail="请求体需含 uphold 字段")
    try:
        from services.trust_learning_service import (
            TrustAppealService,
        )
        return await TrustAppealService().decide_appeal(
            appeal_id, bool(body.get("uphold")),
            str(body.get("note") or ""))
    except Exception as e:
        raise _handle(e) from e


@router.get("/attribution/{trust_id}/{event_id}")
async def attribution(trust_id: int, event_id: int):
    """信值变动归因报告(可解释性强制; LLM 三态)"""
    try:
        from services.trust_learning_service import (
            TrustAppealService,
        )
        return await TrustAppealService().attribution(
            trust_id, event_id)
    except Exception as e:
        raise _handle(e) from e


@router.post("/learning/collect")
async def learning_collect(
    x_role: str = Header(default="", alias="X-Role")):
    """裁决真值批量回流(已裁决未回流申诉 → 第28档案)"""
    _require_admin(x_role)
    try:
        from services.trust_learning_service import (
            TrustLearningService,
        )
        return await TrustLearningService(
        ).collect_appeal_feedback()
    except Exception as e:
        raise _handle(e) from e


@router.post("/learning/run")
async def learning_run(
    x_role: str = Header(default="", alias="X-Role")):
    """触发一轮 Hedge 学习(第28档案; 层内宪法护栏)"""
    _require_admin(x_role)
    try:
        from services.trust_learning_service import (
            TrustLearningService,
        )
        return await TrustLearningService().run_learning()
    except Exception as e:
        raise _handle(e) from e


@router.get("/learning/status")
async def learning_status(
    x_role: str = Header(default="", alias="X-Role")):
    """学习状态视图(档案/申诉统计/权重/宪法护栏)"""
    _require_admin(x_role)
    try:
        from services.trust_learning_service import (
            TrustLearningService,
        )
        return await TrustLearningService().learning_status()
    except Exception as e:
        raise _handle(e) from e


@router.post("/patches")
async def apply_patch(body: dict,
                      x_role: str = Header(default="",
                                           alias="X-Role")):
    """伦理补丁注入(β 映射更新, 版本留痕)

    body: {kind: beta_update, payload: {factor, repairKind,
    beta, label?, category}, note?}
    """
    _require_admin(x_role)
    if not isinstance(body, dict):
        raise HTTPException(status_code=409, detail="请求体需为对象")
    try:
        from services.trust_learning_service import (
            TrustPatchService,
        )
        return await TrustPatchService().apply_patch(
            str(body.get("kind") or ""),
            body.get("payload") or {},
            str(body.get("note") or ""))
    except (TypeError, ValueError) as e:
        raise _handle(e) from e
    except Exception as e:
        raise _handle(e) from e


@router.get("/patches")
async def list_patches(
    x_role: str = Header(default="", alias="X-Role")):
    """补丁历史(版本审计)"""
    _require_admin(x_role)
    try:
        from services.trust_learning_service import (
            TrustPatchService,
        )
        return await TrustPatchService().list_patches()
    except Exception as e:
        raise _handle(e) from e


# ============================================================
# P5: 对外服务开放面(五类 API + 看板聚合)
# ============================================================

@router.get("/open/query/{trust_id}")
async def open_query(trust_id: int,
                     x_app_code: str = Header(
                         default="", alias="X-App-Code")):
    """① 信值查询 API(字段级脱敏 + 修复建议摘要)

    开放面走 44号 Key 网关(台账 published 后双头鉴权/
    QPS/日配额); 本端点业务层脱敏先行(证件摘要/因子
    快照不开放)。
    """
    try:
        from services.trust_gateway_service import (
            TrustGatewayService,
        )
        caller = x_app_code or "anonymous"
        return await TrustGatewayService().open_query(
            trust_id, caller)
    except Exception as e:
        raise _handle(e) from e


@router.post("/open/redeem/confirm")
async def open_redeem_confirm(body: dict):
    """② 兑换核销 API(商户端验证 + 销毁 TV)

    body: {redeemId, merchant, idempotencyKey(≥8),
    nonce(≥16), timestamp(秒)}——幂等键重复返回原结果;
    nonce 原子占位防并发双花; 时间窗 ±300s 防重放。
    """
    if not isinstance(body, dict):
        raise HTTPException(status_code=409, detail="请求体需为对象")
    try:
        from services.trust_gateway_service import (
            TrustGatewayService,
        )
        return await TrustGatewayService(
        ).open_redeem_confirm(
            int(body.get("redeemId") or 0),
            str(body.get("merchant") or ""),
            str(body.get("idempotencyKey") or ""),
            str(body.get("nonce") or ""),
            body.get("timestamp") or 0)
    except (TypeError, ValueError) as e:
        raise _handle(e) from e
    except Exception as e:
        raise _handle(e) from e


@router.post("/open/deposits")
async def open_deposits(body: dict):
    """③ 行为存证 API(敏感词过滤 → P1 验真三道关)

    body: {trustId, layer, factor, observed, peerBaseline,
    evidence, summary?, sources?}——数据最小必要红线:
    敏感信息(证件/病历/指纹等)硬拦截。
    """
    if not isinstance(body, dict):
        raise HTTPException(status_code=409, detail="请求体需为对象")
    try:
        from services.trust_gateway_service import (
            TrustGatewayService,
        )
        return await TrustGatewayService().open_deposit(
            int(body.get("trustId") or 0),
            layer=str(body.get("layer") or ""),
            factor=str(body.get("factor") or ""),
            observed=body.get("observed") or 0,
            peer_baseline=body.get("peerBaseline") or 0,
            evidence=str(body.get("evidence") or ""),
            summary=str(body.get("summary") or ""),
            sources=body.get("sources"))
    except (TypeError, ValueError) as e:
        raise _handle(e) from e
    except Exception as e:
        raise _handle(e) from e


@router.post("/open/convert")
async def open_convert(body: dict):
    """④ 信用分转换 API(与本站信用分单向同步)

    body: {trustId, userId, creditPoints, nonce, timestamp}
    ——nonce+时间窗防重放; TV→信用分方向永久禁止。
    """
    if not isinstance(body, dict):
        raise HTTPException(status_code=409, detail="请求体需为对象")
    try:
        from services.trust_gateway_service import (
            TrustGatewayService,
        )
        return await TrustGatewayService().open_convert(
            int(body.get("trustId") or 0),
            int(body.get("userId") or 0),
            body.get("creditPoints") or 0,
            str(body.get("nonce") or ""),
            body.get("timestamp") or 0)
    except (TypeError, ValueError) as e:
        raise _handle(e) from e
    except Exception as e:
        raise _handle(e) from e


@router.get("/open/audit/{trust_id}")
async def open_audit(trust_id: int,
                     x_app_code: str = Header(
                         default="", alias="X-App-Code")):
    """⑤ 监管审计 API(只读视图 + 操作日志全留存)

    返回档案/事件流水/账本/访问日志四视图(脱敏); 本次
    访问自身也留痕(审计可回溯)。
    """
    try:
        from services.trust_gateway_service import (
            TrustGatewayService,
        )
        caller = x_app_code or "regulator"
        return await TrustGatewayService().open_audit(
            trust_id, caller)
    except Exception as e:
        raise _handle(e) from e


@router.get("/open/dashboard")
async def open_dashboard():
    """治理看板聚合(六区块数据源: 档案/雷达/资产/自进化)"""
    try:
        from services.trust_gateway_service import (
            TrustGatewayService,
        )
        return await TrustGatewayService().dashboard()
    except Exception as e:
        raise _handle(e) from e


def register_trust_value_routes(app) -> None:
    """注册45号路由(main.py startup 调用)"""
    app.include_router(router)
