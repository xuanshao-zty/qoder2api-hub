# -*- coding: utf-8 -*-
"""全局出口代理设置的真实 HTTP 回归；不访问外网、用户账号或固定 8790 服务。

    python tests/_test_proxy_settings_api.py

隔离 accounts/usage/tempdir，真实 Handler + 空闲端口 HTTPServer，真实 admin
登录换 X-Panel-Token。探测仅替换目的 URL/超时，始终调用真实 proxy_test 和
opener；本地带认证 HTTP 假代理必须实际收到 GET，请求失败不能假报成功。
覆盖本地目的前记录真实路由选定的业务 URL，按 intl/cn 验证且忽略输入 probe_url。
共享 _suite_head.bootstrap 和 A._opener_for_request 的实现均不修改。
"""
import base64
from collections import deque
from contextlib import ExitStack
import http.server
import json
import os
import socket
import sys
import tempfile
import threading
import traceback
import urllib.error
import urllib.parse
import urllib.request
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _suite_head import ROOT, check, declare_suite, finish

if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

declare_suite(__file__, ["qoder_proxy.py", "qoder_settings.py", "qoder_netproxy.py",
                         "qoder_accounts.py"])

PROXY_ENV_KEYS = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
                  "http_proxy", "https_proxy", "all_proxy", "no_proxy")


def start_server(stack, handler):
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever,
                              kwargs={"poll_interval": 0.02}, daemon=True)
    thread.start()

    def close_server():
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    stack.callback(close_server)
    return server


def local_handler(seen, credentials=None, body=b"local-probe"):
    expected = None if credentials is None else "Basic " + base64.b64encode(
        credentials.encode("utf-8")).decode("ascii")

    class LocalProbe(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def respond(self):
            auth = self.headers.get("Proxy-Authorization", "")
            seen.append({"method": self.command, "path": self.path,
                         "auth": auth, "expected_auth": expected})
            if expected is not None and auth != expected:
                self.send_response(407)
                self.send_header("Proxy-Authenticate", 'Basic realm="local-test"')
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        do_HEAD = respond
        do_GET = respond

    return LocalProbe


def without_proxy(data):
    return {key: value for key, value in data.items() if key != "outbound_proxy"}


def main():
    with tempfile.TemporaryDirectory(prefix="qd-proxy-settings-api-") as temp:
        isolated_env = {key: "" for key in PROXY_ENV_KEYS}
        isolated_env.update({"ACCOUNTS_DIR": os.path.join(temp, "accounts"),
                             "QD_PROXY_USAGE_DIR": os.path.join(temp, "usage"),
                             "QD_NATIVE_IDENTITY": "0"})
        with mock.patch.dict(os.environ, isolated_env):
            # 不用 bootstrap()：它的 setdefault 不能覆盖调用方已有的用户账号目录。
            import qoder_accounts as A
            import qoder_netproxy as N
            import qoder_proxy as P
            import qoder_settings as S

            original_a_opener = A._opener_for_request
            original_http_json = A.http_json
            original_probe = N.proxy_test
            old_globals = {name: getattr(P, name) for name in (
                "ACCOUNTS_DIR", "USAGE_DIR", "USAGE_LOG", "USAGE_SUMMARY", "PANEL",
                "POOL", "SCHEDULER", "API_KEY", "API_KEY_FILE_SET", "LOG_BUFFER",
                "_LOG_COUNTER", "_login_attempts", "_CLIENT_CTX", "CURRENT_REALM")}
            old_resolver = A._PROXY_RESOLVER
            old_cache = A._OPENER_CACHE
            blocked_connections, upstream_calls, probe_calls = [], [], []
            real_connect = socket.create_connection
            probe_target = ["http://probe.invalid/"]

            def loopback_connection(address, *args, **kwargs):
                # 在 DNS 前拦住意外出网；合法探测必须经下面的本地代理。
                if address[0] not in ("127.0.0.1", "::1") or address[1] == 8790:
                    blocked_connections.append(address)
                    raise AssertionError("测试禁止非 loopback 连接: %r" % (address,))
                return real_connect(address, *args, **kwargs)

            def deny_account_upstream(*args, **kwargs):
                upstream_calls.append(True)
                raise AssertionError("代理设置测试不得调用账号上游 http_json")

            def real_local_probe(proxy_url, **kwargs):
                # 先记下生产路由实际传入的 URL；不能让本地覆盖掩盖根路径/错区回归。
                probe_calls.append({"proxy": proxy_url,
                                    "probe_url": kwargs.get("probe_url"),
                                    "opener": kwargs.get("opener")})
                # 不是假成功：只改探测目的与超时，保留真实 proxy_test/open/read。
                kwargs.update(probe_url=probe_target[0], timeout=1)
                return original_probe(proxy_url, **kwargs)

            with ExitStack() as stack:
                replacements = {
                    "ACCOUNTS_DIR": isolated_env["ACCOUNTS_DIR"],
                    "USAGE_DIR": isolated_env["QD_PROXY_USAGE_DIR"],
                    "USAGE_LOG": os.path.join(temp, "usage", "usage.jsonl"),
                    "USAGE_SUMMARY": os.path.join(temp, "usage", "usage-summary.json"),
                    "PANEL": S.PanelSessions(lambda: P.ACCOUNTS_DIR),
                    "POOL": None, "SCHEDULER": None,
                    "API_KEY": "fixture-api-key-only", "API_KEY_FILE_SET": False,
                    "LOG_BUFFER": deque(maxlen=2000), "_LOG_COUNTER": 0,
                    "_login_attempts": {}, "_CLIENT_CTX": threading.local(),
                    "CURRENT_REALM": "cn",
                }
                for name, value in replacements.items():
                    stack.enter_context(mock.patch.object(P, name, value))
                stack.enter_context(mock.patch.object(
                    A, "_PROXY_RESOLVER", lambda: S.outbound_proxy(P.ACCOUNTS_DIR)))
                stack.enter_context(mock.patch.object(A, "_OPENER_CACHE", {}))
                stack.enter_context(mock.patch.object(
                    A, "http_json", side_effect=deny_account_upstream))
                stack.enter_context(mock.patch.object(
                    N, "proxy_test", new=real_local_probe))
                stack.enter_context(mock.patch.object(
                    socket, "create_connection", new=loopback_connection))

                panel_seen, env_seen, direct_seen = [], [], []
                panel_password = "panel-local-p4ss@word"
                env_password = "environment-local-p5ss"
                panel_server = start_server(stack, local_handler(
                    panel_seen, "panel-user:" + panel_password, b"panel-proxy"))
                env_server = start_server(stack, local_handler(
                    env_seen, "env-user:" + env_password, b"environment-proxy"))
                direct_server = start_server(stack, local_handler(direct_seen))
                api_server = start_server(stack, P.Handler)
                ports = [server.server_address[1] for server in (
                    panel_server, env_server, direct_server, api_server)]
                check("所有 HTTPServer 使用独立空闲端口，不接触 8790",
                      len(set(ports)) == 4 and 8790 not in ports, ports)

                # 绑定但不 listen，避免先关闭再复用空闲端口的竞态。
                dead_socket = stack.enter_context(socket.socket())
                dead_socket.bind(("127.0.0.1", 0))
                dead_url = "http://127.0.0.1:%d" % dead_socket.getsockname()[1]
                panel_url = "http://panel-user:%s@127.0.0.1:%d" % (
                    urllib.parse.quote(panel_password, safe=""), ports[0])
                env_url = "http://env-user:%s@127.0.0.1:%d" % (
                    env_password, ports[1])
                for key in ("HTTP_PROXY", "HTTPS_PROXY"):
                    os.environ[key] = env_url
                # 客户端访问测试 API 显式直连，不能被环境代理或系统代理接管。
                client = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                api_base = "http://127.0.0.1:%d" % ports[3]

                def request(path, payload=None, headers=None):
                    raw = None if payload is None else json.dumps(payload).encode("utf-8")
                    req_headers = {"Connection": "close"}
                    if raw is not None:
                        req_headers["Content-Type"] = "application/json"
                    req_headers.update(headers or {})
                    req = urllib.request.Request(api_base + path, data=raw,
                                                 headers=req_headers)
                    try:
                        response = client.open(req, timeout=8)
                    except urllib.error.HTTPError as exc:
                        response = exc
                    with response:
                        return int(response.code), json.loads(
                            response.read().decode("utf-8"))

                def hides_secrets(data):
                    text = json.dumps(data, ensure_ascii=False)
                    return all(secret not in text for secret in (
                        panel_password, urllib.parse.quote(panel_password, safe=""),
                        env_password, panel_url, env_url))

                S.save(P.ACCOUNTS_DIR, {
                    "fixture_keep": {"nested": ["keep", 0, False]},
                    "api_keys": [{"id": "fixture-key", "name": "local-only",
                                  "key": "fixture-api-key-only", "enabled": True,
                                  "note": "keep this note"}],
                    "banned_models": ["fixture-blocked-*"],
                    "limits": {"reserve_credits": {"global": 17, "cn": 3}},
                })
                before_login = S.load(P.ACCOUNTS_DIR)
                print("[proxy-settings-api] 真实路由与面板鉴权")
                for label, headers in (
                    ("未登录", {}),
                    ("错误面板 token", {"X-Panel-Token": "invalid-token"}),
                    ("有效 API Bearer 不能代替面板登录",
                     {"Authorization": "Bearer fixture-api-key-only"}),
                ):
                    for route, payload in (
                        ("/settings/save", {"outbound_proxy": panel_url}),
                        ("/settings/proxy-test", {"proxy": panel_url}),
                    ):
                        status, data = request(route, payload, headers)
                        check(label + " " + route + " -> 401",
                              status == 401 and bool(data.get("error")), (status, data))
                check("拒绝未授权请求时不保存、不调用探测、不接触代理",
                      S.load(P.ACCOUNTS_DIR) == before_login and not probe_calls
                      and not panel_seen and not env_seen)
                status, login = request("/panel/login", {"password": "admin"})
                token = login.get("token", "")
                check("真实 /panel/login admin 登录换签名 token",
                      status == 200 and login.get("ok") is True
                      and token.count(".") == 2 and P.PANEL.valid(token), login)
                if not token:
                    return
                auth = {"X-Panel-Token": token}

                def check_realm_probe(realm, source, payload, received):
                    before_calls, before_requests = len(probe_calls), len(received)
                    malicious_url = "https://attacker-probe.invalid/not-a-business-probe"
                    body = dict(payload, probe_url=malicious_url)
                    with mock.patch.object(P, "CURRENT_REALM", realm):
                        status, tested = request("/settings/proxy-test", body, auth)
                    calls = probe_calls[before_calls:]
                    expected = A.get_realm_config(realm)["openapi"] + A.PATH_USERINFO
                    selected = calls[0].get("probe_url") if len(calls) == 1 else None
                    parts = urllib.parse.urlsplit(selected or "")
                    label = "%s / %s" % (realm, source)
                    check(label + " 生产路由显式选择本区 userinfo，非根且忽略恶意 probe_url",
                          selected == expected and selected != malicious_url
                          and parts.scheme == "https" and bool(parts.netloc)
                          and parts.path == A.PATH_USERINFO and parts.path != "/",
                          {"selected": selected, "expected": expected, "calls": len(calls)})
                    check(label + " 真实路由返回当前 realm 与正确出口来源",
                          status == 200 and tested.get("ok") is True
                          and tested.get("realm") == realm
                          and tested.get("source") == source, (status, tested))
                    expected_path = (urllib.parse.urlsplit(probe_target[0]).path
                                     if source == "direct" else probe_target[0])
                    check(label + " 仍用真实探测/opener 向本地出口发送 GET",
                          len(received) == before_requests + 1
                          and received[-1]["method"] == "GET"
                          and received[-1]["path"] == expected_path,
                          received[before_requests:])

                baseline = without_proxy(S.load(P.ACCOUNTS_DIR))
                status, _ = request("/settings/save?token=" + token,
                                    {"outbound_proxy": panel_url})
                check("token 放 URL 而非 X-Panel-Token 仍拒绝保存", status == 401, status)

                print("[proxy-settings-api] 保存、掩码、非法值原子拒绝")
                status, saved = request("/settings/save", {"outbound_proxy": panel_url}, auth)
                view = saved.get("outbound_proxy", {})
                check("带认证代理串可保存并持久化原值",
                      status == 200 and S.outbound_proxy(P.ACCOUNTS_DIR) == panel_url,
                      (status, saved))
                check("保存响应只展示 masked，不泄露明文或 URL 编码密码",
                      view.get("set") is True and "***@127.0.0.1:" in view.get("masked", "")
                      and hides_secrets(saved), saved)
                check("保存代理保留已有 API Key、限额和未知嵌套键",
                      without_proxy(S.load(P.ACCOUNTS_DIR)) == baseline)
                status, settings = request("/settings", headers=auth)
                check("GET /settings 真实 header 会话可读且代理状态一致",
                      status == 200 and settings.get("outbound_proxy") == view,
                      (status, settings))
                check("GET /settings 面板与环境代理都不泄露密码",
                      settings.get("outbound_proxy_env", {}).get("set") is True
                      and "***@" in settings.get("outbound_proxy_env", {}).get("masked", "")
                      and hides_secrets(settings), settings)
                status, _ = request("/settings")
                check("GET /settings 未登录仍返回 401", status == 401, status)

                persisted = S.load(P.ACCOUNTS_DIR)
                for label, invalid in (
                    ("不支持协议", "ftp://127.0.0.1:21"),
                    ("无效端口", "http://127.0.0.1:not-a-port"),
                    ("附带路径", "http://127.0.0.1:1234/path"),
                    ("控制字符", "http://bad\nuser:pass@127.0.0.1:1234"),
                    ("对象类型", {"host": "127.0.0.1", "port": 1234}),
                ):
                    status, error = request("/settings/save", {
                        "outbound_proxy": invalid, "auth_disabled": True}, auth)
                    check(label + " -> 400 且带错误原因",
                          status == 400 and bool(error.get("error")), (status, error))
                    check(label + " 拒绝后原代理与其它配置原样保留",
                          S.load(P.ACCOUNTS_DIR) == persisted)

                print("[proxy-settings-api] 真探测，认证成功 / 407 / 死端口")
                before = len(panel_seen)
                status, tested = request("/settings/proxy-test", {}, auth)
                check("空输入测试已保存代理，真实探测成功且 source=panel",
                      status == 200 and tested.get("ok") is True
                      and tested.get("source") == "panel"
                      and tested.get("status") == 200
                      and isinstance(tested.get("ms"), int), (status, tested))
                check("成功不是假绿：本地代理实际收到带认证的绝对 URL GET",
                      len(panel_seen) == before + 1
                      and panel_seen[-1]["method"] == "GET"
                      and panel_seen[-1]["path"] == "http://probe.invalid/"
                      and panel_seen[-1]["auth"] == panel_seen[-1]["expected_auth"],
                      panel_seen[-1:] if panel_seen else [])
                check("面板代理优先环境代理，探测响应也只含掩码",
                      not env_seen and tested.get("masked") == view.get("masked")
                      and hides_secrets(tested), tested)
                with A._opener_for_request().open(
                        urllib.request.Request("http://probe.invalid/runtime"), timeout=2) as resp:
                    runtime_body = resp.read()
                check("真实 A opener 读到持久化全局代理并经本地代理发请求",
                      A._opener_for_request is original_a_opener
                      and runtime_body == b"panel-proxy"
                      and panel_seen[-1]["path"] == "http://probe.invalid/runtime")

                before = len(env_seen)
                status, tested = request("/settings/proxy-test", {"proxy": env_url}, auth)
                check("输入代理覆盖保存值，经另一真代理请求且 source=input",
                      status == 200 and tested.get("ok") is True
                      and tested.get("source") == "input"
                      and len(env_seen) == before + 1
                      and env_seen[-1]["method"] == "GET"
                      and env_seen[-1]["auth"] == env_seen[-1]["expected_auth"], tested)
                check("代理测试不会保存输入、也不泄露认证密码",
                      S.load(P.ACCOUNTS_DIR) == persisted and hides_secrets(tested), tested)
                wrong_auth = "http://panel-user:wrong-password@127.0.0.1:%d" % ports[0]
                status, tested = request("/settings/proxy-test", {"proxy": wrong_auth}, auth)
                check("真实代理认证失败 407 不会报成功",
                      status == 200 and tested.get("ok") is False
                      and tested.get("status") == 407 and bool(tested.get("error")), tested)
                status, tested = request("/settings/proxy-test", {"proxy": dead_url}, auth)
                check("127.0.0.1 未监听死端口真实失败，不能全局 mock 成功",
                      status == 200 and tested.get("ok") is False
                      and bool(tested.get("error")) and tested.get("source") == "input",
                      (status, tested))
                check("失败测试后原持久化代理不变", S.load(P.ACCOUNTS_DIR) == persisted)

                print("[proxy-settings-api] intl/cn 真路由业务探测目标：面板 / 输入")
                for realm in ("intl", "cn"):
                    check_realm_probe(realm, "panel", {}, panel_seen)
                    check_realm_probe(realm, "input", {"proxy": env_url}, env_seen)
                check("按区探测和恶意 probe_url 均不改变已保存代理或其它设置",
                      S.load(P.ACCOUNTS_DIR) == persisted)

                print("[proxy-settings-api] 清除回落环境，再验证真实直连探测")
                status, cleared = request("/settings/save", {"outbound_proxy": ""}, auth)
                check("清除删除面板代理，响应回落环境变量",
                      status == 200 and S.outbound_proxy(P.ACCOUNTS_DIR) == ""
                      and "outbound_proxy" not in S.load(P.ACCOUNTS_DIR)
                      and cleared.get("outbound_proxy", {}).get("set") is False
                      and cleared.get("outbound_proxy_env", {}).get("set") is True,
                      (status, cleared))
                check("清除只改变代理，原数据的其它所有键完整保留",
                      without_proxy(S.load(P.ACCOUNTS_DIR)) == baseline)
                check("清除响应也不泄露环境密码", hides_secrets(cleared), cleared)
                before = len(env_seen)
                status, tested = request("/settings/proxy-test", {}, auth)
                check("清除后 API 真探测走环境代理而非旧面板代理",
                      status == 200 and tested.get("ok") is True
                      and tested.get("source") == "environment"
                      and tested.get("direct") is False
                      and len(env_seen) == before + 1
                      and env_seen[-1]["path"] == "http://probe.invalid/", tested)
                with A._opener_for_request().open(
                        urllib.request.Request("http://probe.invalid/after-clear"), timeout=2) as resp:
                    runtime_body = resp.read()
                check("清除刷新真实 A opener，后续请求确实回落环境代理",
                      A._opener_for_request is original_a_opener
                      and runtime_body == b"environment-proxy"
                      and env_seen[-1]["path"] == "http://probe.invalid/after-clear")

                print("[proxy-settings-api] intl/cn 真路由业务探测目标：环境")
                for realm in ("intl", "cn"):
                    check_realm_probe(realm, "environment", {}, env_seen)

                for key in PROXY_ENV_KEYS:
                    os.environ[key] = ""
                direct_opener = A._opener_for_request()
                probe_target[0] = "http://127.0.0.1:%d/direct-probe" % ports[2]
                # 只在直连用例局部 mock 获取入口，返回真正的直连 opener，仍真发 GET。
                with mock.patch.object(A, "_opener_for_request",
                                       return_value=direct_opener) as lookup:
                    status, tested = request("/settings/proxy-test", {}, auth)
                    check("未设代理仍调用真实探测 opener，不短路假成功",
                          status == 200 and tested.get("ok") is True
                          and tested.get("direct") is True
                          and tested.get("source") == "direct"
                          and tested.get("masked") == "" and lookup.call_count == 1
                          and len(direct_seen) == 1
                          and direct_seen[0]["method"] == "GET"
                          and direct_seen[0]["path"] == "/direct-probe", tested)
                    probe_target[0] = dead_url + "/direct-fails"
                    status, tested = request("/settings/proxy-test", {}, auth)
                    check("未设代理但真实直连失败时 API 必须 ok=false",
                          status == 200 and tested.get("ok") is False
                          and tested.get("direct") is True
                          and tested.get("source") == "direct"
                          and bool(tested.get("error")) and lookup.call_count == 2, tested)
                check("局部直连用例结束后真实 A opener 函数已恢复",
                      A._opener_for_request is original_a_opener)
                probe_target[0] = "http://127.0.0.1:%d/direct-probe" % ports[2]
                print("[proxy-settings-api] intl/cn 真路由业务探测目标：直连")
                for realm in ("intl", "cn"):
                    check_realm_probe(realm, "direct", {}, direct_seen)
                check("账号上游 http_json 调用计数为 0", len(upstream_calls) == 0,
                      len(upstream_calls))
                check("非 loopback 连接尝试计数为 0（不访问外网或真实代理）",
                      len(blocked_connections) == 0, blocked_connections)
                check("代理相关日志不包含密码",
                      hides_secrets([entry["msg"] for entry in P.LOG_BUFFER]))

            check("所有替换的生产 globals / resolver / cache / probe 均恢复",
                  all(getattr(P, name) is value for name, value in old_globals.items())
                  and A._PROXY_RESOLVER is old_resolver
                  and A._OPENER_CACHE is old_cache
                  and A._opener_for_request is original_a_opener
                  and A.http_json is original_http_json
                  and socket.create_connection is real_connect
                  and N.proxy_test is original_probe)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        check("套件执行不应有未处理异常", False, "%s: %s" % (type(exc).__name__, exc))
        traceback.print_exc()
    finish()
