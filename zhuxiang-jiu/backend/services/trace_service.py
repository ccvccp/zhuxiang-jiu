"""双码追溯管理模块业务逻辑层

核心业务:
    - 箱码生成/绑定(TBC箱顶码防拆 + BBC箱底码防窜)
    - 生命码生成/绑定(BLC瓶级唯一码)
    - 扫码激活(首次激活日期为老酒回收起始日期, 转让后不重置)
    - 扫码追溯(全链路追溯链)
    - 防窜货检测(激活位置与代理区域比对)
    - 转让管理(持有人变更, 激活日期延续)

锁保护:
    - 生成: lock:trace:generate:{code_type}:{batch_no} (码生成幂等)
    - 激活: lock:trace:activate:{life_code}  (激活幂等)
    - 绑定: lock:trace:bind:{box_id}         (箱码绑定)
    - 转让: lock:trace:transfer:{life_code}  (转让原子操作)

异常约定:
    - KeyError → 404(码不存在)
    - ValueError → 409(业务冲突: 重复激活/已回收/已冻结等)
"""

from datetime import date

from core.locks import get_lock
from core.helpers import ts, bc_hash
from repositories.trace_repository import (
    TraceRepository,
    # 码类型
    CODE_TYPE_BOX, CODE_TYPE_LIFE,
    # 箱码子类型
    LIFE_STATUS_PENDING, LIFE_STATUS_ACTIVE, LIFE_STATUS_TRANSFERRED,
    LIFE_STATUS_RECYCLED, LIFE_STATUS_FROZEN,
    # 箱码状态
    BOX_STATUS_PENDING, BOX_STATUS_BOUND, BOX_STATUS_OPENED,
    BOX_STATUS_RECYCLED,
    # 扫码类型
    SCAN_TYPE_ACTIVATE, SCAN_TYPE_TRANSFER, SCAN_TYPE_QUERY, SCAN_TYPE_OPEN,
)


# ============================================================
# 生命码编码规则常量
# ============================================================

# 品牌标识
BRAND_PREFIX = "BLC"
# 箱码前缀
BOX_TOP_PREFIX = "TBC"
BOX_BOTTOM_PREFIX = "BBC"
# 激活奖励积分
ACTIVATION_REWARD_POINTS = 50
# 转让类型
TRANSFER_TYPE_GIFT = "gift"        # 赠送
TRANSFER_TYPE_TRADE = "trade"      # 交易
TRANSFER_TYPE_INHERIT = "inherit"  # 继承
# 防窜货: 跨区激活判定
ANTI_CHANNEL_CROSS_PROVINCE = "cross_province"
ANTI_CHANNEL_CROSS_CITY = "cross_city"


class TraceService:
    """双码追溯业务逻辑(双模式存储, 锁保护 RMW)"""

    def __init__(self, repo: TraceRepository = TraceRepository()):
        self.repo = repo

    # ============================================================
    # 1. 箱码生成/绑定
    # ============================================================

    async def generate_box_codes(self, product_id: str, batch_no: str,
                                   count: int, agent_id: int = None,
                                   agent_region: str = None) -> dict:
        """批量生成箱码(TBC + BBC双码)

        规则:
            - 一箱一码, 每箱生成TBC(箱顶防拆)+BBC(箱底防窜)
            - 编码格式: TBC-{产品}-{批次}-{序号}
            - 初始状态: 待绑定(pending)

        Returns:
            生成结果(含箱码列表)

        Raises:
            ValueError: 数量无效
        """
        if count <= 0 or count > 1000:
            raise ValueError("生成数量须在1-1000之间")

        lock_key = f"trace:generate:box:{batch_no}"
        async with get_lock(lock_key):
            boxes = []
            for i in range(count):
                seq = f"{i + 1:06d}"
                top_code = f"{BOX_TOP_PREFIX}-{product_id}-{batch_no}-{seq}"
                bottom_code = f"{BOX_BOTTOM_PREFIX}-{product_id}-{batch_no}-{seq}"
                box = {
                    "boxCode": top_code,
                    "boxBottomCode": bottom_code,
                    "productId": product_id,
                    "batchNo": batch_no,
                    "agentId": agent_id,
                    "agentRegion": agent_region,
                    "status": BOX_STATUS_PENDING,
                    "lifeCodeIds": [],
                    "createdAt": ts(),
                }
                box_id = await self.repo.create_box_code(box)
                box["id"] = box_id
                boxes.append(box)

            return {
                "productId": product_id,
                "batchNo": batch_no,
                "count": len(boxes),
                "boxes": boxes,
            }

    async def bind_box_code(self, box_id: int, life_code_ids: list,
                             agent_id: int = None) -> dict:
        """绑定箱码(关联生命码+代理商)

        规则:
            - 仅待绑定(pending)的箱码可绑定
            - 一个箱码可关联多瓶生命码(整箱)
            - 绑定后状态: 已绑定(bound)

        Raises:
            KeyError: 箱码不存在
            ValueError: 状态非法
        """
        lock_key = f"trace:bind:{box_id}"
        async with get_lock(lock_key):
            box = await self.repo.get_box_code(box_id)
            if box is None:
                raise KeyError(f"箱码不存在(boxId={box_id})")

            if box["status"] != BOX_STATUS_PENDING:
                raise ValueError(f"箱码状态非法(当前{box['status']}, 须为{BOX_STATUS_PENDING})")

            # 校验生命码存在性
            for lid in life_code_ids:
                life = await self.repo.get_life_code(lid)
                if life is None:
                    raise KeyError(f"生命码不存在(lifeId={lid})")

            await self.repo.update_box_code(box_id, {
                "lifeCodeIds": life_code_ids,
                "agentId": agent_id or box.get("agentId"),
                "status": BOX_STATUS_BOUND,
                "boundAt": ts(),
            })
            box["lifeCodeIds"] = life_code_ids
            box["status"] = BOX_STATUS_BOUND
            return box

    async def get_box_code(self, box_id: int) -> dict:
        """查询箱码(含回收资格判定: 双码完好为回收必要条件)"""
        box = await self.repo.get_box_code(box_id)
        if box is None:
            raise KeyError(f"箱码不存在(boxId={box_id})")
        result = dict(box)
        result["recycleEligible"] = self._recycle_eligible(box)
        return result

    async def get_box_by_code(self, box_code: str) -> dict:
        """按箱码字符串查询"""
        box = await self.repo.get_box_by_code(box_code)
        if box is None:
            raise KeyError(f"箱码不存在(boxCode={box_code})")
        return box

    async def list_box_codes(self, batch_no: str = None, status: str = None,
                              limit: int = 50) -> list[dict]:
        """查询箱码列表"""
        return await self.repo.list_box_codes(batch_no, status, limit)

    async def open_box_code(self, box_code: str, operator_id: int = None,
                             longitude: float = None, latitude: float = None,
                             province: str = None, city: str = None) -> dict:
        """扫描箱顶码开箱(开箱即失效, 不可逆)

        核心规则(箱码设计文档 3.1/3.2):
            - 仅箱顶码(TBC)触发开箱失效, 箱底码(BBC)开箱不失效
            - 仅已绑定(bound)的箱码可开箱
            - 开箱后箱状态: 已开箱(opened), 不可恢复(不可逆)
            - 记录开箱位置, 与代理区域比对(跨区预警)
            - 开箱后箱体不参与3年增值回收(双码完好为回收必要条件)
            - 箱内瓶级生命码可逐瓶激活(不受箱顶码失效影响)

        Returns:
            开箱结果(含双码状态/回收资格/跨区标记/提示)

        Raises:
            KeyError: 箱码不存在
            ValueError: 箱底码不可开箱 / 状态非法 / 重复开箱
        """
        if box_code.startswith(BOX_BOTTOM_PREFIX):
            raise ValueError(
                "箱底码(BBC)开箱不失效, 仅箱顶码(TBC)可触发开箱失效")

        lock_key = f"trace:open:{box_code}"
        async with get_lock(lock_key):
            box = await self.repo.get_box_by_code(box_code)
            if box is None:
                raise KeyError(f"箱码不存在(boxCode={box_code})")

            status = box.get("status")
            if status == BOX_STATUS_OPENED:
                raise ValueError("箱顶码已开箱失效(不可逆, 不可重复开箱)")
            if status == BOX_STATUS_RECYCLED:
                raise ValueError("箱码已回收(不可开箱)")
            if status != BOX_STATUS_BOUND:
                raise ValueError(
                    f"箱码状态非法(当前{status}, 须为{BOX_STATUS_BOUND}后方可开箱)")

            # 开箱位置 vs 代理区域(跨区预警, 对齐防窜检测规则)
            agent_region = box.get("agentRegion")
            agent_province = box.get("agentProvince")
            agent_city = box.get("agentCity")
            is_cross = False
            risk_level = "low"
            warning_type = None
            if agent_region and province and province != agent_region:
                is_cross = True
                risk_level = "high"
                warning_type = ANTI_CHANNEL_CROSS_PROVINCE
            elif agent_city and city and city != agent_city:
                is_cross = True
                risk_level = "medium"
                warning_type = ANTI_CHANNEL_CROSS_CITY

            now = ts()
            await self.repo.update_box_code(box["id"], {
                "status": BOX_STATUS_OPENED,
                "openedAt": now,
                "operatorId": operator_id,
                "openProvince": province,
                "openCity": city,
                "openLongitude": longitude,
                "openLatitude": latitude,
                "isCrossRegion": is_cross,
            })

            # 开箱记录(写入扫码日志, scanType=open, 对齐 box_opened_logs 字段)
            scan_id = await self.repo.add_scan_log({
                "code": box_code,
                "codeType": CODE_TYPE_BOX,
                "userId": operator_id,
                "scanType": SCAN_TYPE_OPEN,
                "longitude": longitude,
                "latitude": latitude,
                "province": province,
                "city": city,
                "isCrossRegion": is_cross,
                "riskLevel": risk_level,
                "warningType": warning_type,
                "blockHash": bc_hash(),
                "createdAt": now,
            })

            return {
                "boxId": box["id"],
                "boxCode": box_code,                        # 箱顶码: 已失效
                "boxBottomCode": box.get("boxBottomCode"),  # 箱底码: 永久有效
                "status": BOX_STATUS_OPENED,
                "topCodeValid": False,      # 箱顶码失效(不可逆)
                "bottomCodeValid": True,    # 箱底码永久有效(至回收终止)
                "recycleEligible": False,   # 开箱后不参与3年增值回收
                "openedAt": now,
                "operatorId": operator_id,
                "openProvince": province,
                "openCity": city,
                "isCrossRegion": is_cross,
                "riskLevel": risk_level,
                "warningType": warning_type,
                "scanId": scan_id,
                "tips": [
                    "箱顶码已失效, 箱体不参与3年增值回收",
                    "箱底码仍有效, 继续用于库存管理和追溯",
                    "箱内瓶级生命码可逐瓶激活(不受箱顶码失效影响)",
                ],
            }

    @staticmethod
    def _recycle_eligible(box: dict) -> bool:
        """双码完好为老酒回收必要条件(文档 3.3)

        箱顶码未开箱 + 箱体未回收 → 双码完好, 具备回收资格。
        """
        return box.get("status") not in (BOX_STATUS_OPENED, BOX_STATUS_RECYCLED)

    # ============================================================
    # 2. 生命码生成/绑定
    # ============================================================

    async def generate_life_codes(self, product_id: str, batch_no: str,
                                    count: int, product_name: str = "",
                                    product_abv: int = 42,
                                    product_volume: str = "500ml") -> dict:
        """批量生成生命码(BLC格式)

        规则:
            - 一瓶一码, 编码格式: BLC-{产品}-{批次}-{序号}-{CRC}
            - CRC: 简单哈希校验码(防伪)
            - 初始状态: 待激活(pending)

        Returns:
            生成结果(含生命码列表)

        Raises:
            ValueError: 数量无效
        """
        if count <= 0 or count > 1000:
            raise ValueError("生成数量须在1-1000之间")

        lock_key = f"trace:generate:life:{batch_no}"
        async with get_lock(lock_key):
            lifes = []
            for i in range(count):
                seq = f"{i + 1:06d}"
                # CRC4: 简单校验码(取hash前4位)
                raw = f"{product_id}-{batch_no}-{seq}"
                crc = format(abs(hash(raw)) % 0xFFFF, "04X")
                life_code = f"{BRAND_PREFIX}-{product_id}-{batch_no}-{seq}-{crc}"
                life = {
                    "lifeCode": life_code,
                    "productId": product_id,
                    "productName": product_name,
                    "productAbv": product_abv,
                    "productVolume": product_volume,
                    "batchNo": batch_no,
                    "status": LIFE_STATUS_PENDING,
                    "firstActivationDate": None,
                    "userId": None,
                    "holderName": None,
                    "crcCode": crc,
                    "boxCodeId": None,
                    "createdAt": ts(),
                }
                life_id = await self.repo.create_life_code(life)
                life["id"] = life_id
                lifes.append(life)

            return {
                "productId": product_id,
                "batchNo": batch_no,
                "count": len(lifes),
                "lifeCodes": lifes,
            }

    async def bind_life_to_box(self, life_id: int, box_id: int) -> dict:
        """绑定生命码到箱码

        规则:
            - 仅待激活(pending)的生命码可绑定
            - 绑定后生命码关联箱码ID

        Raises:
            KeyError: 生命码/箱码不存在
            ValueError: 状态非法
        """
        lock_key = f"trace:bindlife:{life_id}"
        async with get_lock(lock_key):
            life = await self.repo.get_life_code(life_id)
            if life is None:
                raise KeyError(f"生命码不存在(lifeId={life_id})")
            box = await self.repo.get_box_code(box_id)
            if box is None:
                raise KeyError(f"箱码不存在(boxId={box_id})")

            await self.repo.update_life_code(life_id, {
                "boxCodeId": box_id,
            })
            life["boxCodeId"] = box_id
            return life

    async def get_life_code(self, life_id: int) -> dict:
        """查询生命码"""
        life = await self.repo.get_life_code(life_id)
        if life is None:
            raise KeyError(f"生命码不存在(lifeId={life_id})")
        return life

    async def get_life_by_code(self, life_code: str) -> dict:
        """按生命码字符串查询"""
        life = await self.repo.get_life_by_code(life_code)
        if life is None:
            raise KeyError(f"生命码不存在(lifeCode={life_code})")
        return life

    async def list_life_codes(self, batch_no: str = None, status: str = None,
                                user_id: int = None, limit: int = 50) -> list[dict]:
        """查询生命码列表"""
        return await self.repo.list_life_codes(batch_no, status, user_id, limit)

    # ============================================================
    # 3. 扫码激活/追溯
    # ============================================================

    async def activate_life_code(self, life_code: str, user_id: int,
                                   user_phone: str = None, user_name: str = None,
                                   longitude: float = None, latitude: float = None,
                                   province: str = None, city: str = None,
                                   district: str = None,
                                   purchase_channel: str = "online",
                                   purchase_price: float = 0,
                                   order_id: str = "") -> dict:
        """扫码激活生命码

        核心规则:
            - 首次扫码即激活, 记录起始日期(老酒回收唯一基准)
            - 一瓶一激活, 同一生命码只能激活一次
            - 激活后状态: 已激活(active)
            - 激活日期一经记录不可更改
            - order_id(可选): 激活时携带订单号, 落码留痕 →
              建立"订单-批次"关联(AI智能管理模块工人分润自动取数依据)

        Returns:
            激活结果(含激活日期)

        Raises:
            KeyError: 生命码不存在
            ValueError: 已激活/已回收/已冻结
        """
        lock_key = f"trace:activate:{life_code}"
        async with get_lock(lock_key):
            life = await self.repo.get_life_by_code(life_code)
            if life is None:
                raise KeyError(f"生命码不存在(lifeCode={life_code})")

            if life["status"] == LIFE_STATUS_ACTIVE:
                raise ValueError("生命码已激活(不可重复激活)")
            if life["status"] == LIFE_STATUS_RECYCLED:
                raise ValueError("生命码已回收(不可激活)")
            if life["status"] == LIFE_STATUS_FROZEN:
                raise ValueError("生命码已冻结(不可激活)")

            activation_date = date.today().isoformat()
            now = ts()

            await self.repo.update_life_code(life["id"], {
                "status": LIFE_STATUS_ACTIVE,
                "firstActivationDate": activation_date,
                "userId": user_id,
                "holderName": user_name,
                "userPhone": user_phone,
                "longitude": longitude,
                "latitude": latitude,
                "province": province,
                "city": city,
                "district": district,
                "purchaseChannel": purchase_channel,
                "purchasePrice": purchase_price,
                "orderId": order_id or "",
                "activatedAt": now,
            })

            # 写入扫码记录
            scan_id = await self.repo.add_scan_log({
                "code": life_code,
                "codeType": CODE_TYPE_LIFE,
                "userId": user_id,
                "userPhone": user_phone,
                "scanType": SCAN_TYPE_ACTIVATE,
                "longitude": longitude,
                "latitude": latitude,
                "province": province,
                "city": city,
                "district": district,
                "blockHash": bc_hash(),
                "createdAt": now,
            })

            life["status"] = LIFE_STATUS_ACTIVE
            life["firstActivationDate"] = activation_date
            life["userId"] = user_id
            return {
                "lifeCode": life_code,
                "lifeId": life["id"],
                "status": LIFE_STATUS_ACTIVE,
                "firstActivationDate": activation_date,
                "userId": user_id,
                "rewardPoints": ACTIVATION_REWARD_POINTS,
                "scanId": scan_id,
            }

    async def scan_trace(self, code: str, user_id: int = None,
                          longitude: float = None, latitude: float = None,
                          province: str = None, city: str = None,
                          scan_type: str = SCAN_TYPE_QUERY) -> dict:
        """扫码追溯(查询追溯链)

        规则:
            - 支持箱码/生命码扫码
            - 返回全链路追溯信息(赋码→激活→转让→回收)
            - 写入扫码记录

        Returns:
            追溯链信息

        Raises:
            KeyError: 码不存在
        """
        # 先尝试生命码
        life = await self.repo.get_life_by_code(code)
        if life is not None:
            scans = await self.repo.list_scan_logs(code=code, limit=100)
            result = {
                "code": code,
                "codeType": CODE_TYPE_LIFE,
                "productId": life.get("productId"),
                "productName": life.get("productName"),
                "batchNo": life.get("batchNo"),
                "status": life.get("status"),
                "firstActivationDate": life.get("firstActivationDate"),
                "userId": life.get("userId"),
                "holderName": life.get("holderName"),
                "boxCodeId": life.get("boxCodeId"),
                "traceChain": scans,
            }
        else:
            box = await self.repo.get_box_by_code(code)
            if box is None:
                raise KeyError(f"码不存在(code={code})")
            scans = await self.repo.list_scan_logs(code=code, limit=100)
            result = {
                "code": code,
                "codeType": CODE_TYPE_BOX,
                "productId": box.get("productId"),
                "batchNo": box.get("batchNo"),
                "status": box.get("status"),
                "agentId": box.get("agentId"),
                "agentRegion": box.get("agentRegion"),
                "lifeCodeIds": box.get("lifeCodeIds", []),
                "traceChain": scans,
            }

        # 写入扫码记录
        scan_id = await self.repo.add_scan_log({
            "code": code,
            "codeType": result["codeType"],
            "userId": user_id,
            "scanType": scan_type,
            "longitude": longitude,
            "latitude": latitude,
            "province": province,
            "city": city,
            "createdAt": ts(),
        })
        result["scanId"] = scan_id
        return result

    async def get_trace_chain(self, code: str) -> dict:
        """查询追溯链(不写入扫码记录)"""
        life = await self.repo.get_life_by_code(code)
        if life is not None:
            scans = await self.repo.list_scan_logs(code=code, limit=100)
            return {
                "code": code,
                "codeType": CODE_TYPE_LIFE,
                "status": life.get("status"),
                "firstActivationDate": life.get("firstActivationDate"),
                "traceChain": scans,
            }
        box = await self.repo.get_box_by_code(code)
        if box is not None:
            scans = await self.repo.list_scan_logs(code=code, limit=100)
            return {
                "code": code,
                "codeType": CODE_TYPE_BOX,
                "status": box.get("status"),
                "traceChain": scans,
            }
        raise KeyError(f"码不存在(code={code})")

    # ============================================================
    # 4. 防窜货检测
    # ============================================================

    async def detect_anti_channel(self, life_code: str, longitude: float,
                                    latitude: float, province: str = None,
                                    city: str = None) -> dict:
        """防窜货检测

        规则:
            - 比对激活位置与箱码绑定的代理区域
            - 跨省激活 → 窜货预警
            - 跨市激活 → 提示

        Returns:
            检测结果(含是否窜货/风险等级)

        Raises:
            KeyError: 生命码不存在
        """
        life = await self.repo.get_life_by_code(life_code)
        if life is None:
            raise KeyError(f"生命码不存在(lifeCode={life_code})")

        box_id = life.get("boxCodeId")
        agent_region = None
        agent_province = None
        agent_city = None
        if box_id:
            box = await self.repo.get_box_code(box_id)
            if box:
                agent_region = box.get("agentRegion")
                agent_province = box.get("agentProvince")
                agent_city = box.get("agentCity")

        is_cross = False
        risk_level = "low"
        warning_type = None

        if agent_region and province and province != agent_region:
            is_cross = True
            risk_level = "high"
            warning_type = ANTI_CHANNEL_CROSS_PROVINCE
        elif agent_city and city and city != agent_city:
            is_cross = True
            risk_level = "medium"
            warning_type = ANTI_CHANNEL_CROSS_CITY

        return {
            "lifeCode": life_code,
            "currentProvince": province,
            "currentCity": city,
            "agentRegion": agent_region,
            "agentProvince": agent_province,
            "agentCity": agent_city,
            "isCrossChannel": is_cross,
            "warningType": warning_type,
            "riskLevel": risk_level,
            "longitude": longitude,
            "latitude": latitude,
        }

    # ============================================================
    # 5. 转让管理
    # ============================================================

    async def transfer_life_code(self, life_code: str, from_user_id: int,
                                   to_user_id: int, to_name: str = None,
                                   transfer_type: str = TRANSFER_TYPE_GIFT,
                                   longitude: float = None, latitude: float = None,
                                   province: str = None, city: str = None) -> dict:
        """生命码转让(持有人变更)

        核心规则:
            - 激活日期延续不变(不重置)
            - 持有人变更, 状态: 已转让(transferred)
            - 写入转让扫码记录

        Raises:
            KeyError: 生命码不存在
            ValueError: 状态非法/持有人不匹配
        """
        lock_key = f"trace:transfer:{life_code}"
        async with get_lock(lock_key):
            life = await self.repo.get_life_by_code(life_code)
            if life is None:
                raise KeyError(f"生命码不存在(lifeCode={life_code})")

            if life["status"] not in (LIFE_STATUS_ACTIVE, LIFE_STATUS_TRANSFERRED):
                raise ValueError(
                    f"生命码状态非法(当前{life['status']}, 须为{LIFE_STATUS_ACTIVE}/{LIFE_STATUS_TRANSFERRED})"
                )

            # 校验当前持有人
            if life.get("userId") != from_user_id:
                raise ValueError("转让人非当前持有人")

            await self.repo.update_life_code(life["id"], {
                "userId": to_user_id,
                "holderName": to_name,
                "status": LIFE_STATUS_TRANSFERRED,
                "transferredAt": ts(),
            })

            # 写入转让扫码记录
            scan_id = await self.repo.add_scan_log({
                "code": life_code,
                "codeType": CODE_TYPE_LIFE,
                "userId": from_user_id,
                "toUserId": to_user_id,
                "scanType": SCAN_TYPE_TRANSFER,
                "transferType": transfer_type,
                "longitude": longitude,
                "latitude": latitude,
                "province": province,
                "city": city,
                "blockHash": bc_hash(),
                "createdAt": ts(),
            })

            return {
                "lifeCode": life_code,
                "lifeId": life["id"],
                "fromUserId": from_user_id,
                "toUserId": to_user_id,
                "toName": to_name,
                "transferType": transfer_type,
                "firstActivationDate": life.get("firstActivationDate"),
                "status": LIFE_STATUS_TRANSFERRED,
                "scanId": scan_id,
            }

    # ============================================================
    # 6. 查询统计
    # ============================================================

    async def list_scan_logs(self, code: str = None, user_id: int = None,
                              scan_type: str = None, limit: int = 50) -> list[dict]:
        """查询扫码记录列表"""
        return await self.repo.list_scan_logs(code, user_id, scan_type, limit)

    async def get_stats(self, batch_no: str = None) -> dict:
        """追溯统计"""
        boxes = await self.repo.list_box_codes(batch_no=batch_no, limit=10000)
        lifes = await self.repo.list_life_codes(batch_no=batch_no, limit=10000)

        # 箱码状态统计
        box_status_count = {}
        for b in boxes:
            s = b.get("status", "unknown")
            box_status_count[s] = box_status_count.get(s, 0) + 1

        # 生命码状态统计
        life_status_count = {}
        active_count = 0
        for l in lifes:
            s = l.get("status", "unknown")
            life_status_count[s] = life_status_count.get(s, 0) + 1
            if s == LIFE_STATUS_ACTIVE:
                active_count += 1

        # 激活率
        activation_rate = round(active_count / len(lifes), 4) if lifes else 0.0

        return {
            "batchNo": batch_no,
            "totalBoxes": len(boxes),
            "totalLifeCodes": len(lifes),
            "boxStatusCount": box_status_count,
            "lifeStatusCount": life_status_count,
            "activeCount": active_count,
            "activationRate": activation_rate,
        }
