"""41号·AI智能代驾模块·司机资格与审查(设计文档 §2.2)

自营轨道(本站超级会员注册, AI 全自动审查):
    L5 竹海SVIP 硬门槛 → 材料硬校验(驾龄≥3年/证件格式)
    → DriverApplicationScorer AI 审查(第22档案, 五因子)
    → 三档: ≥70 自动通过(approved, 直接入池)
             50-70 人工复核队列(manual_review, admin 裁决)
             <50 拒绝(rejected, 留痕可申诉)
    → 通过后入司机池(offline 初始, 上线接单走 online)

加盟轨道: P2 落地(admin 批量导入 + AI 抽查复核), P0 预留常量。

异常约定(遵循项目约定):
    - KeyError → 404(会员/申请/司机不存在)
    - ValueError → 409(门槛不达标/材料缺失/重复申请/状态非法)
"""

import logging
from datetime import datetime, UTC

from core.locks import get_lock
from repositories.ride_repository import (
    RideRepository,
    TRACK_SELF, TRACK_NAMES,
    DRIVER_STATUS_ONLINE, DRIVER_STATUS_OFFLINE,
    DRIVER_STATUS_SUSPENDED, DRIVER_STATUS_REVOKED,
    APP_STATUS_APPROVED, APP_STATUS_MANUAL_REVIEW,
    APP_STATUS_REJECTED,
    APP_AUTO_SCORE, APP_MANUAL_SCORE,
    MIN_DRIVING_YEARS, VALID_LICENSE_CLASSES,
)
from services.ai_scoring_service import SCORERS


logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


class DriverGateService:
    """司机资格 AI 审查与司机池管理"""

    def __init__(self):
        self.repo = RideRepository()

    # --------------------------------------------------------
    # 材料硬校验(评分前置, 一票否决)
    # --------------------------------------------------------

    @staticmethod
    def _validate_profile(profile: dict) -> None:
        """材料硬校验: 驾龄/准驾车型/证件格式

        Raises:
            ValueError: 材料缺失或不达标
        """
        id_number = str(profile.get("idNumber") or "").strip()
        if len(id_number) != 18:
            raise ValueError("身份证号须为 18 位")
        license_number = str(profile.get("licenseNumber") or "").strip()
        if len(license_number) < 10:
            raise ValueError("驾照号格式不合规(长度不足)")
        years = profile.get("drivingYears")
        try:
            years = float(years)
        except (TypeError, ValueError):
            raise ValueError("驾龄缺失或格式非法")
        if years < MIN_DRIVING_YEARS:
            raise ValueError(f"驾龄不足 {MIN_DRIVING_YEARS} 年, 不符合代驾员硬门槛")
        license_class = str(profile.get("licenseClass") or "C1").upper()
        if license_class not in VALID_LICENSE_CLASSES:
            raise ValueError(f"准驾车型 {license_class} 不在允许范围"
                             f"({'/'.join(VALID_LICENSE_CLASSES)})")

    @staticmethod
    def _member_age(member: dict) -> int:
        """从出生日期推算年龄(缺省 0 → 评分器按中性处理)"""
        birth = str(member.get("birthdate") or "")
        try:
            born = datetime.strptime(birth, "%Y-%m-%d")
            return max(0, int((datetime.now(UTC).date() - born.date()).days / 365))
        except ValueError:
            return 0

    @staticmethod
    def _register_hours(member: dict) -> float:
        """注册至今年时数(缺省 720 中性)"""
        created = str(member.get("created_at") or "")
        try:
            start = datetime.fromisoformat(created)
            return max(0.0, (datetime.now(UTC) - start).total_seconds() / 3600)
        except ValueError:
            return 720.0

    # --------------------------------------------------------
    # 注册申请(AI 全自动审查)
    # --------------------------------------------------------

    async def apply(self, member_id: int, profile: dict) -> dict:
        """超级会员提交代驾员注册申请 → AI 审查即时出档

        Raises:
            KeyError: 会员不存在
            ValueError: 非SVIP/材料不达标/重复申请
        """
        from repositories.member_repository import MemberRepository

        member_id = int(member_id)
        async with get_lock(f"ride:driver:apply:{member_id}"):
            member = await MemberRepository().get_by_id(member_id)
            if not member:
                raise KeyError(f"会员 {member_id} 不存在")
            level = int(member.get("level") or 1)
            if level < 5:
                raise ValueError("代驾员资格为竹海SVIP专属, "
                                 f"当前等级 L{level} 不满足(L5 硬门槛)")
            existing = await self.repo.get_application_by_member(member_id)
            if existing is not None:
                raise ValueError(
                    f"该会员已有审查流水(applicationId="
                    f"{existing.get('applicationId')}, "
                    f"状态 {existing.get('status')}), 不可重复申请")

            self._validate_profile(profile)

            application_id = await self.repo.next_id("application")
            ctx = {
                "applicationId": application_id,
                "memberId": member_id,
                "idNumber": str(profile.get("idNumber") or ""),
                "licenseNumber": str(profile.get("licenseNumber") or ""),
                "licenseClass": str(profile.get("licenseClass") or "C1"),
                "drivingYears": profile.get("drivingYears"),
                "age": self._member_age(member),
                "ageVerified": bool(member.get("ageVerified")),
                "registerHours": self._register_hours(member),
                "bambooScore": profile.get("bambooScore")
                or member.get("bambooScore") or 600,
                "complaintRate": profile.get("complaintRate") or 0,
                "accidentFreeDecl": bool(profile.get("accidentFreeDecl")),
                "drunkFreeDecl": bool(profile.get("drunkFreeDecl")),
                "emergencyContact": str(profile.get("emergencyContact") or ""),
            }
            scoring = await SCORERS["driver_application_gate"].score(ctx)
            status = scoring["action"]   # approved/manual_review/rejected
            application = {
                "applicationId": application_id,
                "memberId": member_id,
                "nickname": member.get("nickname", ""),
                "phone": member.get("phone", ""),
                "profile": {
                    "idNumber": ctx["idNumber"],
                    "licenseNumber": ctx["licenseNumber"],
                    "licenseClass": ctx["licenseClass"],
                    "drivingYears": ctx["drivingYears"],
                    "accidentFreeDecl": ctx["accidentFreeDecl"],
                    "drunkFreeDecl": ctx["drunkFreeDecl"],
                    "emergencyContact": ctx["emergencyContact"],
                },
                "status": status,
                "score": scoring["score"],
                "scoreSnapshot": scoring,
                "reviewNote": "",
                "reviewer": "",
                "driverId": None,
                "appliedAt": _now_iso(),
                "decidedAt": _now_iso(),
            }
            await self.repo.save_application(application)

            # 自动通过 → 直接入司机池(offline 初始, 自行上线)
            if status == APP_STATUS_APPROVED:
                driver = await self._create_driver(application)
                application["driverId"] = driver["driverId"]
                await self.repo.save_application(application)

            logger.info("ride_driver_applied member=%s application=%s "
                        "score=%s status=%s", member_id, application_id,
                        scoring["score"], status)
            return {
                "success": True,
                "applicationId": application_id,
                "memberId": member_id,
                "status": status,
                "score": scoring["score"],
                "levelName": scoring["levelName"],
                "driverId": application.get("driverId"),
                "scoring": scoring,
            }

    async def _create_driver(self, application: dict) -> dict:
        """审查通过 → 入司机池(自营轨道, offline 初始)"""
        driver_id = await self.repo.next_driver_id()
        profile = application.get("profile") or {}
        driver = {
            "driverId": driver_id,
            "track": TRACK_SELF,
            "trackName": TRACK_NAMES[TRACK_SELF],
            "platform": "本站",
            "name": application.get("nickname") or f"会员{application.get('memberId')}",
            "phone": application.get("phone", ""),
            "plateNo": "",
            "drivingYears": profile.get("drivingYears") or 0,
            "licenseClass": profile.get("licenseClass") or "C1",
            "rating": 5.0,           # 新司机满星初始
            "completedOrders": 0,
            "acceptRate": 1.0,
            "cancelRate": 0.0,
            "status": DRIVER_STATUS_OFFLINE,
            "city": "泰安",
            "lat": 36.19,           # 默认位置(泰安市区中心), 上线前可经 profile 更新
            "lng": 117.13,
            "currentRideId": "",
            "todayOrders": 0,
            "memberId": int(application.get("memberId") or 0),
            "applicationId": int(application.get("applicationId") or 0),
            "suspendedReason": "",
            "createdAt": _now_iso(),
            "updatedAt": _now_iso(),
        }
        await self.repo.save_driver(driver)
        return driver

    # --------------------------------------------------------
    # 查询
    # --------------------------------------------------------

    async def get_application(self, member_id: int) -> dict:
        """会员查询自己的审查进度

        Raises:
            KeyError: 无申请记录
        """
        app = await self.repo.get_application_by_member(int(member_id))
        if app is None:
            raise KeyError(f"会员 {member_id} 无代驾员申请记录")
        return app

    async def list_applications(self, status: str = None,
                                limit: int = 100) -> list[dict]:
        return await self.repo.list_applications(status=status, limit=limit)

    async def list_pool(self, track: str = None, status: str = None,
                        limit: int = 200) -> list[dict]:
        return await self.repo.list_drivers(track=track, status=status,
                                            limit=limit)

    # --------------------------------------------------------
    # 人工复核(manual_review 档裁决)
    # --------------------------------------------------------

    async def decide(self, application_id: int, approve: bool,
                     reviewer: str = "admin", note: str = "") -> dict:
        """人工复核裁决: manual_review → approved/rejected

        Raises:
            KeyError: 申请不存在
            ValueError: 状态非人工复核档
        """
        app = await self.repo.get_application(int(application_id))
        if app is None:
            raise KeyError(f"审查流水 {application_id} 不存在")
        if app.get("status") != APP_STATUS_MANUAL_REVIEW:
            raise ValueError(f"申请状态为 {app.get('status')}, "
                             "仅人工复核队列可裁决")
        app["status"] = APP_STATUS_APPROVED if approve else APP_STATUS_REJECTED
        app["reviewer"] = reviewer
        app["reviewNote"] = note
        app["decidedAt"] = _now_iso()
        if approve:
            driver = await self._create_driver(app)
            app["driverId"] = driver["driverId"]
        await self.repo.save_application(app)
        logger.info("ride_driver_decided application=%s approve=%s "
                    "reviewer=%s", application_id, approve, reviewer)
        return app

    # --------------------------------------------------------
    # 司机上下线/状态管理
    # --------------------------------------------------------

    async def set_driver_status(self, member_id: int, status: str,
                                reason: str = "") -> dict:
        """司机状态流转: online⇄offline / suspended(违规) / revoked(吊销)

        Raises:
            KeyError: 会员无司机资格
            ValueError: 状态非法/流转非法
        """
        if status not in ("online", "offline", "suspended", "revoked"):
            raise ValueError(f"非法司机状态: {status}")
        driver = await self.repo.get_driver_by_member(int(member_id))
        if driver is None:
            raise KeyError(f"会员 {member_id} 无代驾员资格")
        current = driver.get("status")
        if current in (DRIVER_STATUS_REVOKED,):
            raise ValueError("资格已吊销, 不可再变更状态")
        if current == DRIVER_STATUS_SUSPENDED and status != DRIVER_STATUS_REVOKED:
            raise ValueError("暂停中司机仅支持吊销(需 admin 恢复)")
        if status == DRIVER_STATUS_ONLINE and driver.get("plateNo") == "":
            raise ValueError("请先补充车辆牌照信息后再上线接单")
        driver["status"] = status
        if status == DRIVER_STATUS_SUSPENDED:
            driver["suspendedReason"] = reason or "违规暂停"
        driver["updatedAt"] = _now_iso()
        await self.repo.save_driver(driver)
        return driver

    async def update_driver(self, member_id: int, fields: dict) -> dict:
        """司机补充信息(牌照等)

        Raises:
            KeyError: 会员无司机资格
            ValueError: 字段非法
        """
        allowed = {"plateNo", "city", "lat", "lng"}
        updates = {k: v for k, v in (fields or {}).items() if k in allowed}
        if not updates:
            raise ValueError(f"无可更新字段(允许: {sorted(allowed)})")
        driver = await self.repo.get_driver_by_member(int(member_id))
        if driver is None:
            raise KeyError(f"会员 {member_id} 无代驾员资格")
        driver.update(updates)
        driver["updatedAt"] = _now_iso()
        await self.repo.save_driver(driver)
        return driver

    # --------------------------------------------------------
    # 报表
    # --------------------------------------------------------

    async def overview(self) -> dict:
        """司机池与审查概览(管理端看板)"""
        drivers = await self.repo.list_drivers(limit=1000)
        apps = await self.repo.list_applications(limit=1000)
        by_track = {}
        for track in (TRACK_SELF, "partner", "platform"):
            by_track[track] = sum(1 for d in drivers if d.get("track") == track)
        return {
            "success": True,
            "poolTotal": len(drivers),
            "byTrack": by_track,
            "onlineCount": sum(1 for d in drivers
                               if d.get("status") == DRIVER_STATUS_ONLINE),
            "applications": {
                "total": len(apps),
                "approved": sum(1 for a in apps
                                if a.get("status") == APP_STATUS_APPROVED),
                "manualReview": sum(1 for a in apps
                                    if a.get("status") == APP_STATUS_MANUAL_REVIEW),
                "rejected": sum(1 for a in apps
                                if a.get("status") == APP_STATUS_REJECTED),
            },
            "thresholds": {"auto": APP_AUTO_SCORE, "manual": APP_MANUAL_SCORE},
        }
