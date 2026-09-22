"""TTS 毒反代——cogtts 流式故障注入(Toxiproxy 思想本地化)

借鉴故障注入文档: 通用 Chaos Monkey 不覆盖流式管道, 需对
TTS 出站链注入「延迟/截断/断连/慢流」验证后端容错。选型
说明: 文档首选 Toxiproxy, 但本场景上游为 HTTPS(智谱)且
LLM_BASE_URL 覆写为 http://127.0.0.1 时证书域不匹配——
自研标准库反代做同款毒(TCP 层语义等价, 零第三方依赖,
宿主/容器通吃):

    normal     透传(对照基线)
    delay2s    首包延迟 2s(≈toxiproxy latency 毒)
    cut        转发 40% 后 RST 断连(≈slice 毒)——合成一半失败
    abort      收请求立即断连(连接级故障——urllib 快速异常路径)
    500        返回 500 JSON(上游服务故障)
    slow_drip  每 8KB 停 300ms(≈bandwidth 毒)——慢流不断

用法(独立进程, 严禁生产容器内改 LLM_BASE_URL——本代理仅
被验证脚本进程以 env 覆写指向, 不影响生产出站):
    FAULT_MODE=cut python fault_proxy.py            # :9999
    FAULT_MODE=delay2s PORT=9930 python fault_proxy.py

客户端对接: LLM_BASE_URL=http://127.0.0.1:9999/api/paas/v4
"""
import json
import os
import time
import urllib.request
from http.server import (BaseHTTPRequestHandler,
                         ThreadingHTTPServer)

UPSTREAM = "https://open.bigmodel.cn"
MODE = os.environ.get("FAULT_MODE", "normal")
PORT = int(os.environ.get("PORT", "9999"))


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass  # 静默(毒代理不刷屏)

    def _rst(self):
        """直接断连(RST)——客户端 read() 抛异常走容错"""
        try:
            self.connection.close()
        except Exception:
            pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n) if n else b""

        if MODE == "500":
            payload = json.dumps(
                {"error": {"code": "500",
                           "message": "injected: upstream failure"}}
            ).encode("utf-8")
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        if MODE == "abort":
            self._rst()
            return

        target = UPSTREAM + self.path
        req = urllib.request.Request(
            target, data=body, method="POST",
            headers={"Content-Type": "application/json",
                     "Authorization":
                         self.headers.get("Authorization", "")})
        try:
            up = urllib.request.urlopen(req, timeout=15)
        except Exception:
            self.send_response(502)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        data = up.read()
        ctype = up.headers.get("Content-Type", "audio/wav")

        if MODE == "delay2s":
            time.sleep(2.0)  # 首包延迟(响应整体晚到)
        if MODE == "cut":
            # 转发 40% 后断连: 无 Content-Length + Connection
            # close → 客户端按 EOF 读; 中途 RST → read() 抛错
            head = data[: max(1, int(len(data) * 0.4))]
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(head)
            self.wfile.flush()
            time.sleep(0.15)  # 让已写数据先抵达
            self._rst()
            return
        if MODE == "slow_drip":
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Connection", "close")
            self.end_headers()
            for i in range(0, len(data), 8192):
                self.wfile.write(data[i:i + 8192])
                self.wfile.flush()
                time.sleep(0.3)
            self.wfile.flush()
            self.connection.close()  # 写完优雅收尾(慢流不断)
            return
        # normal / delay2s: 整体透传
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def main():
    print(f"[fault_proxy] mode={MODE} listen=:{PORT} "
          f"upstream={UPSTREAM}")
    ThreadingHTTPServer(("127.0.0.1", PORT),
                        Handler).serve_forever()


if __name__ == "__main__":
    main()
