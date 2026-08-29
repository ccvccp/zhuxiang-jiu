"""会员管理模块单元测试

覆盖 14 个端点:
  - /api/member/register|login|login/bonus  (注册/登录/每日奖励)
  - /api/member/profile|password            (资料/密码)
  - /api/member/level|consume               (等级/消费)
  - /api/member/points|points/deduct        (积分查询/抵扣)
  - /api/member/addresses (CRUD)            (收货地址)

测试维度:
  - 成功路径 / 错误路径 (401/404/409)
  - 请求校验 (参数缺失 / 格式错误)
  - 权限守卫 (X-Member-Id 缺失 → 401)
  - 业务规则 (手机号唯一 / 密码错误 / 积分不足 / 等级自动升级)
  - 状态持久化 (store 回查)

运行: pytest test_member_routes.py -v
"""

import pytest
from fastapi.testclient import TestClient

from main import app, _mock_store
from repositories.store import reset_store

client = TestClient(app)


@pytest.fixture(autouse=True)
def _reset_member_store():
    """每个测试前重置 store 到初始状态(会员1: phone=13800000001, pwd=test123456)"""
    reset_store()
    yield


# ============================================================
#  注册: /api/member/register
# ============================================================

class TestMemberRegister:
    def test_register_success(self):
        """正常注册: 赠送 100 积分"""
        resp = client.post("/api/member/register", json={
            "phone": "13900000000", "password": "abc123456", "nickname": "新用户",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["phone"] == "13900000000"
        assert data["nickname"] == "新用户"
        assert data["level"] == 1
        assert data["levelName"] == "竹芽会员"
        assert data["points"] == 100
        assert "token" in data
        assert any("100" in log["msg"] for log in data["logs"])

    def test_register_duplicate_phone(self):
        """手机号已注册: 409"""
        resp = client.post("/api/member/register", json={
            "phone": "13800000001", "password": "abc123456",
        })
        assert resp.status_code == 409
        assert "已注册" in resp.json()["error"]

    def test_register_short_password(self):
        """密码过短: 422"""
        resp = client.post("/api/member/register", json={
            "phone": "13900000000", "password": "123",
        })
        assert resp.status_code == 422

    def test_register_bad_phone(self):
        """手机号格式错误: 409"""
        resp = client.post("/api/member/register", json={
            "phone": "123", "password": "abc123456",
        })
        assert resp.status_code == 409

    def test_register_default_nickname(self):
        """未传昵称: 自动生成(竹香用户+后4位)"""
        resp = client.post("/api/member/register", json={
            "phone": "13900000000", "password": "abc123456",
        })
        assert resp.status_code == 200
        assert resp.json()["nickname"] == "竹香用户0000"

    def test_register_persisted(self):
        """注册后数据持久化到 store"""
        resp = client.post("/api/member/register", json={
            "phone": "13900000000", "password": "abc123456",
        })
        member_id = resp.json()["memberId"]
        assert member_id in _mock_store["members"]
        assert _mock_store["members"][member_id]["phone"] == "13900000000"

    # ---------- 酒类合规年龄验证(P0-1) ----------

    def test_register_minor_rejected(self):
        """未满18周岁出生日期 → 409 拒绝注册"""
        resp = client.post("/api/member/register", json={
            "phone": "13900000000", "password": "abc123456",
            "birthdate": "2015-01-01",
        })
        assert resp.status_code == 409
        assert "18" in resp.json()["error"]

    def test_register_bad_birthdate_format(self):
        """出生日期格式非法 → 409"""
        resp = client.post("/api/member/register", json={
            "phone": "13900000000", "password": "abc123456",
            "birthdate": "1990/01/01",
        })
        assert resp.status_code == 409

    def test_register_adult_birthdate_verified(self):
        """成年出生日期 → 注册成功且 ageVerified=true"""
        resp = client.post("/api/member/register", json={
            "phone": "13900000000", "password": "abc123456",
            "birthdate": "1990-01-01",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["ageVerified"] is True
        assert data["ageConfirmed"] is False

    def test_register_age_confirmed_persisted(self):
        """ageConfirmed 声明落库(供下单年龄门复用)"""
        resp = client.post("/api/member/register", json={
            "phone": "13900000000", "password": "abc123456",
            "ageConfirmed": True,
        })
        assert resp.status_code == 200
        member_id = resp.json()["memberId"]
        assert _mock_store["members"][member_id]["ageConfirmed"] is True


# ============================================================
#  登录: /api/member/login
# ============================================================

class TestMemberLogin:
    def test_login_success(self):
        """正常登录: 返回 token"""
        resp = client.post("/api/member/login", json={
            "phone": "13800000001", "password": "test123456",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["memberId"] == 1
        assert data["phone"] == "13800000001"
        assert "token" in data

    def test_login_wrong_password(self):
        """密码错误: 409"""
        resp = client.post("/api/member/login", json={
            "phone": "13800000001", "password": "wrongpwd",
        })
        assert resp.status_code == 409
        assert "密码错误" in resp.json()["error"]

    def test_login_phone_not_registered(self):
        """手机号未注册: 404"""
        resp = client.post("/api/member/login", json={
            "phone": "19999999999", "password": "abc123456",
        })
        assert resp.status_code == 404

    def test_login_disabled_account(self):
        """账号禁用: 409"""
        _mock_store["members"][1]["status"] = 0
        resp = client.post("/api/member/login", json={
            "phone": "13800000001", "password": "test123456",
        })
        assert resp.status_code == 409
        assert "禁用" in resp.json()["error"]

    def test_login_bonus(self):
        """每日登录奖励: +5 积分"""
        resp = client.post("/api/member/login/bonus", headers={"X-Member-Id": "1"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["addedPoints"] == 5
        assert data["totalPoints"] == 105

    def test_login_bonus_no_auth(self):
        """每日奖励未登录: 401"""
        resp = client.post("/api/member/login/bonus")
        assert resp.status_code == 401


# ============================================================
#  资料: /api/member/profile
# ============================================================

class TestMemberProfile:
    def test_get_profile_success(self):
        """获取资料: 不返回密码"""
        resp = client.get("/api/member/profile", headers={"X-Member-Id": "1"})
        assert resp.status_code == 200
        profile = resp.json()["profile"]
        assert profile["id"] == 1
        assert profile["phone"] == "13800000001"
        assert "password" not in profile
        assert profile["levelName"] == "竹芽会员"
        assert "nextLevelGrowth" in profile

    def test_get_profile_no_auth(self):
        """未登录: 401"""
        resp = client.get("/api/member/profile")
        assert resp.status_code == 401

    def test_get_profile_not_found(self):
        """会员不存在: 404"""
        resp = client.get("/api/member/profile", headers={"X-Member-Id": "999"})
        assert resp.status_code == 404

    def test_update_profile_success(self):
        """修改昵称"""
        resp = client.put("/api/member/profile",
                          json={"nickname": "新昵称", "gender": 2},
                          headers={"X-Member-Id": "1"})
        assert resp.status_code == 200
        assert resp.json()["profile"]["nickname"] == "新昵称"
        assert resp.json()["profile"]["gender"] == 2

    def test_update_profile_no_fields(self):
        """无可更新字段: 409"""
        resp = client.put("/api/member/profile", json={},
                          headers={"X-Member-Id": "1"})
        assert resp.status_code == 409

    def test_change_password_success(self):
        """修改密码"""
        resp = client.put("/api/member/password", json={
            "old_password": "test123456", "new_password": "newpwd123",
        }, headers={"X-Member-Id": "1"})
        assert resp.status_code == 200

    def test_change_password_wrong_old(self):
        """旧密码错误: 409"""
        resp = client.put("/api/member/password", json={
            "old_password": "wrong", "new_password": "newpwd123",
        }, headers={"X-Member-Id": "1"})
        assert resp.status_code == 409

    def test_change_password_short_new(self):
        """新密码过短: 422"""
        resp = client.put("/api/member/password", json={
            "old_password": "test123456", "new_password": "123",
        }, headers={"X-Member-Id": "1"})
        assert resp.status_code == 422


# ============================================================
#  等级与消费: /api/member/level, /api/member/consume
# ============================================================

class TestMemberLevel:
    def test_get_level_success(self):
        """查询等级"""
        resp = client.get("/api/member/level", headers={"X-Member-Id": "1"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["level"] == 1
        assert data["levelName"] == "竹芽会员"
        assert data["growthValue"] == 0
        assert data["nextLevelGrowth"] == 500
        assert data["thresholds"]["2"] == 500

    def test_consume_and_level_up(self):
        """消费触发升级 L1→L2 (消费 500)"""
        resp = client.post("/api/member/consume", json={"amount": 500},
                           headers={"X-Member-Id": "1"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["growthValue"] == 500
        assert data["points"] == 600  # 100 初始 + 500 消费
        assert data["fromLevel"] == 1
        assert data["toLevel"] == 2
        assert data["leveledUp"] is True
        assert data["levelName"] == "竹叶会员"

    def test_consume_no_level_up(self):
        """消费不足升级: 保持 L1"""
        resp = client.post("/api/member/consume", json={"amount": 100},
                           headers={"X-Member-Id": "1"})
        assert resp.status_code == 200
        assert resp.json()["toLevel"] == 1
        assert resp.json()["leveledUp"] is False

    def test_consume_large_amount_multi_level(self):
        """大额消费跨级升级 L1→L4 (消费 6999)"""
        resp = client.post("/api/member/consume", json={"amount": 6999},
                           headers={"X-Member-Id": "1"})
        assert resp.status_code == 200
        assert resp.json()["toLevel"] == 4
        assert resp.json()["levelName"] == "竹海 VIP"

    def test_consume_zero_amount(self):
        """消费金额 0: 409"""
        resp = client.post("/api/member/consume", json={"amount": 0},
                           headers={"X-Member-Id": "1"})
        assert resp.status_code == 422  # gt=0 校验

    def test_consume_no_auth(self):
        """未登录消费: 401"""
        resp = client.post("/api/member/consume", json={"amount": 100})
        assert resp.status_code == 401

    def test_consume_max_level(self):
        """满级 L5: nextLevelGrowth=0"""
        client.post("/api/member/consume", json={"amount": 9999},
                    headers={"X-Member-Id": "1"})
        resp = client.get("/api/member/level", headers={"X-Member-Id": "1"})
        assert resp.json()["level"] == 5
        assert resp.json()["nextLevelGrowth"] == 0


# ============================================================
#  积分: /api/member/points, /api/member/points/deduct
# ============================================================

class TestMemberPoints:
    def test_get_points_success(self):
        """查询积分"""
        resp = client.get("/api/member/points", headers={"X-Member-Id": "1"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["points"] == 100
        assert data["pointsValue"] == 1.0  # 100 积分 = ¥1

    def test_deduct_points_success(self):
        """积分抵扣"""
        resp = client.post("/api/member/points/deduct", json={"points": 100},
                           headers={"X-Member-Id": "1"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["deductedPoints"] == 100
        assert data["leftPoints"] == 0
        assert data["deductAmount"] == 1.0

    def test_deduct_points_insufficient(self):
        """积分不足: 409"""
        resp = client.post("/api/member/points/deduct", json={"points": 1000},
                           headers={"X-Member-Id": "1"})
        assert resp.status_code == 409
        assert "不足" in resp.json()["error"]

    def test_deduct_points_not_multiple(self):
        """非 100 倍数: 409"""
        resp = client.post("/api/member/points/deduct", json={"points": 50},
                           headers={"X-Member-Id": "1"})
        assert resp.status_code == 409

    def test_deduct_exceeds_limit(self):
        """抵扣超过订单 30% 上限: 409(5000 积分=¥50 > ¥100*30%=¥30)"""
        resp = client.post("/api/member/points/deduct",
                           json={"points": 5000, "order_amount": 100},
                           headers={"X-Member-Id": "1"})
        assert resp.status_code == 409
        assert "上限" in resp.json()["error"]

    def test_deduct_within_limit(self):
        """抵扣在 30% 上限内: 成功"""
        resp = client.post("/api/member/points/deduct",
                           json={"points": 100, "order_amount": 1000},
                           headers={"X-Member-Id": "1"})
        assert resp.status_code == 200


# ============================================================
#  收货地址: /api/member/addresses
# ============================================================

class TestMemberAddresses:
    def test_list_addresses(self):
        """地址列表(初始 1 条)"""
        resp = client.get("/api/member/addresses", headers={"X-Member-Id": "1"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 1
        assert data["addresses"][0]["name"] == "张三"

    def test_add_address_success(self):
        """新增地址"""
        resp = client.post("/api/member/addresses", json={
            "name": "李四", "phone": "13900000000",
            "province": "北京市", "city": "北京市",
            "district": "朝阳区", "detail": "三里屯",
        }, headers={"X-Member-Id": "1"})
        assert resp.status_code == 200
        assert resp.json()["address"]["name"] == "李四"
        assert resp.json()["address"]["is_default"] == 0

    def test_add_address_default_clears_others(self):
        """新增默认地址: 清除旧默认"""
        client.post("/api/member/addresses", json={
            "name": "李四", "phone": "13900000000",
            "province": "北京市", "city": "北京市",
            "district": "朝阳区", "detail": "三里屯",
            "is_default": 1,
        }, headers={"X-Member-Id": "1"})
        # 旧地址应不再是默认
        resp = client.get("/api/member/addresses", headers={"X-Member-Id": "1"})
        addrs = resp.json()["addresses"]
        old = next(a for a in addrs if a["name"] == "张三")
        assert old["is_default"] == 0

    def test_add_address_missing_field(self):
        """缺少字段: 422"""
        resp = client.post("/api/member/addresses", json={
            "name": "李四", "phone": "13900000000",
        }, headers={"X-Member-Id": "1"})
        assert resp.status_code == 422

    def test_update_address_success(self):
        """修改地址"""
        resp = client.put("/api/member/addresses/addr_seed_001", json={
            "name": "张三丰", "detail": "新地址",
        }, headers={"X-Member-Id": "1"})
        assert resp.status_code == 200
        assert resp.json()["address"]["name"] == "张三丰"
        assert resp.json()["address"]["detail"] == "新地址"

    def test_update_address_not_found(self):
        """地址不存在: 404"""
        resp = client.put("/api/member/addresses/no_such", json={"name": "X"},
                          headers={"X-Member-Id": "1"})
        assert resp.status_code == 404

    def test_delete_address_success(self):
        """删除地址"""
        resp = client.delete("/api/member/addresses/addr_seed_001",
                              headers={"X-Member-Id": "1"})
        assert resp.status_code == 200
        # 确认已删除
        resp2 = client.get("/api/member/addresses", headers={"X-Member-Id": "1"})
        assert resp2.json()["count"] == 0

    def test_delete_address_not_found(self):
        """删除不存在地址: 404"""
        resp = client.delete("/api/member/addresses/no_such",
                              headers={"X-Member-Id": "1"})
        assert resp.status_code == 404

    def test_addresses_no_auth(self):
        """未登录: 401"""
        resp = client.get("/api/member/addresses")
        assert resp.status_code == 401
