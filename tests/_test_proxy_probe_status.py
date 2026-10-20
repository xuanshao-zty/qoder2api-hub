# -*- coding: utf-8 -*-
"""业务路径与 HTTP 状态的离线代理探测回归。

    python tests/_test_proxy_probe_status.py

只导入 qoder_netproxy，不加载生产账号或面板密码，不用/修改 bootstrap。
本地 HTTPServer 同时模拟根路径 503、userinfo 401 和代理认证失败 407；
始终调用真实 N.proxy_test / build_opener / opener.open，不访问外网或 8790。
"""
from contextlib import ExitStack
import http.server
import inspect
import os
import socket
import sys
import threading
import traceback
import urllib.parse
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _suite_head import ROOT, check, declare_suite, finish

if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import qoder_netproxy as N

declare_suite(__file__, ["qoder_netproxy.py"])

PROXY_ENV_KEYS = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
                  "http_proxy", "https_proxy", "all_proxy", "no_proxy")


def local_handler(seen):
    statuses = {"/": 503, "/api/v1/userinfo": 401,
                "/forbidden": 403, "/missing": 404,
                "/method-not-allowed": 405, "/proxy-auth": 407,
                "/business-unavailable": 503}

    class LocalStatus(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def respond(self):
            path = urllib.parse.urlsplit(self.path).path
            status = statuses.get(path, 500)
            seen.append({"method": self.command, "url": self.path,
                         "status": status,
                         "authorization": self.headers.get("Authorization")})
            self.send_response(status)
            if status == 407:
                self.send_header("Proxy-Authenticate", 'Basic realm="offline-probe"')
            self.send_header("Content-Length", "0")
            self.send_header("Connection", "close")
            self.end_headers()

        do_GET = respond
        # HEAD 也能得到相同状态，方法回归必须由 GET 断言明确检出。
        do_HEAD = respond

    return LocalStatus


def main():
    expected_default = "https://openapi.qoder.com.cn/api/v1/userinfo"
    original_probe, original_build = N.proxy_test, N.build_opener
    original_connect = socket.create_connection
    old_env = {key: os.environ.get(key) for key in PROXY_ENV_KEYS}
    default_probe = inspect.signature(N.proxy_test).parameters["probe_url"].default
    check("默认 PROBE_URL 是 CN 只读 userinfo 业务接口，不是站点根",
          N.PROBE_URL == expected_default
          and urllib.parse.urlsplit(N.PROBE_URL).path == "/api/v1/userinfo",
          N.PROBE_URL)
    check("真实 proxy_test 的默认参数同步使用业务 URL，而非旧根地址",
          default_probe == expected_default
          and urllib.parse.urlsplit(default_probe).path not in ("", "/"),
          default_probe)

    blocked_connections, seen = [], []

    def loopback_connection(address, *args, **kwargs):
        # 在 DNS 前拦住任何意外外网地址，也禁止接触固定 8790 服务。
        if address[0] not in ("127.0.0.1", "::1") or address[1] == 8790:
            blocked_connections.append(address)
            raise AssertionError("离线探测只允许 loopback 随机端口: %r" % (address,))
        return original_connect(address, *args, **kwargs)

    with mock.patch.dict(os.environ, {key: "" for key in PROXY_ENV_KEYS}):
        with ExitStack() as stack:
            stack.enter_context(mock.patch.object(
                socket, "create_connection", new=loopback_connection))
            server = http.server.ThreadingHTTPServer(
                ("127.0.0.1", 0), local_handler(seen))
            server.daemon_threads = True
            thread = threading.Thread(target=server.serve_forever,
                                      kwargs={"poll_interval": 0.02}, daemon=True)
            thread.start()

            def close_server():
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)

            stack.callback(close_server)
            port = server.server_address[1]
            check("状态服务器只绑定 loopback 空闲端口，不使用 8790",
                  port > 0 and port != 8790, server.server_address)
            proxy_url = "http://127.0.0.1:%d" % port
            results = {}
            cases = (
                ("/", 503, False, "根路径 503 仍失败，不会简单改绿"),
                ("/api/v1/userinfo", 401, True, "userinfo 无账号 Token 的 401 表示连通"),
                ("/forbidden", 403, True, "业务 HTTP 403 仍表示目标已响应"),
                ("/missing", 404, True, "业务 HTTP 404 仍表示目标已响应"),
                ("/method-not-allowed", 405, True, "业务 HTTP 405 仍表示目标已响应"),
                ("/business-unavailable", 503, False, "业务路径 503 也必须失败"),
                ("/proxy-auth", 407, False, "代理认证 407 必须失败"),
            )
            for path, status, expected_ok, label in cases:
                target = "http://probe.invalid" + path
                before = len(seen)
                result = N.proxy_test(proxy_url, probe_url=target, timeout=2)
                results[path] = result
                check(label,
                      result.get("ok") is expected_ok and result.get("status") == status,
                      result)
                check("%s 真实代理收到绝对业务 URL GET，无账号 Authorization" % status,
                      len(seen) == before + 1 and seen[-1]["method"] == "GET"
                      and seen[-1]["url"] == target
                      and seen[-1]["authorization"] is None, seen[before:])
                check("%s 保留真实探测目标、耗时与成功/错误语义" % status,
                      result.get("probe") == target
                      and isinstance(result.get("ms"), int) and result["ms"] >= 0
                      and (not result.get("error") if expected_ok
                           else bool(result.get("error"))), result)

            check("同一真实本地代理：根 503 失败而 userinfo 401 成功",
                  results["/"].get("ok") is False
                  and results["/api/v1/userinfo"].get("ok") is True,
                  {path: results[path] for path in ("/", "/api/v1/userinfo")})
            auth_error = results["/proxy-auth"].get("error", "")
            check("407 错误明确提示代理认证失败，不能混同账号 401",
                  "407" in auth_error and "认证" in auth_error, auth_error)
            check("所有探测都确实经过本地 HTTPServer，非 loopback/8790 尝试为 0",
                  len(seen) == len(cases) and not blocked_connections,
                  {"received": len(seen), "blocked": blocked_connections})

    check("套件恢复 socket 与所有代理环境变量，未替换真实 N 探测/opener",
          socket.create_connection is original_connect
          and N.proxy_test is original_probe and N.build_opener is original_build
          and all(os.environ.get(key) == value for key, value in old_env.items()))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        check("套件执行不应有未处理异常", False, "%s: %s" % (type(exc).__name__, exc))
        traceback.print_exc()
    finish()
