"""阶段3部署验证(代码就位+模拟通道回退): 生产回归"""
import subprocess

import httpx

with httpx.Client(base_url="http://127.0.0.1:8000", timeout=30) as c:
    # 1. 健康探针
    r = c.get("/api/decision/health")
    print("① 健康探针:", r.status_code)
    assert r.status_code == 200

    # 2. 模块导入+回退判定(未配置四变量)
    out = subprocess.run(
        ["python", "-c",
         "from services import sms_aliyun; "
         "print('configured:', sms_aliyun.is_configured())"],
        capture_output=True, text=True)
    print("②", out.stdout.strip() or out.stderr.strip()[:200])
    assert "configured: False" in out.stdout

    # 3. 模拟通道回归: 发码(新号)成功入库
    phone = "13900007777"
    r = c.post("/api/sms/send", json={"phone": phone})
    print("③ sms/send(模拟通道):", r.status_code, r.json().get("msg"))
    assert r.status_code == 200 and r.json().get("success") is True

    # 4. 登录回归(auth_service 改动无影响)
    r = c.post("/api/auth/login",
               json={"phone": "13800000002", "password": "test123456"})
    print("④ 登录回归:", r.status_code)
    assert r.status_code == 200

print("\n=== 阶段3代码部署完成: 模拟通道回退正常, 凭据注入即激活 ===")
