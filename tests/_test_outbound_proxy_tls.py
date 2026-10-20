# -*- coding: utf-8 -*-
"""Real urllib HTTPS proxy regressions; standard library and loopback only.

The fake authenticated proxy terminates TLS itself after CONNECT/SOCKS5.  The
committed self-signed certificate and private key are PUBLIC TEST FIXTURES,
never production credentials.  Clients explicitly trust only that certificate
without disabling hostname or certificate verification.  No bootstrap/opener
replacement is used, and socket/DNS guards prohibit any non-loopback traffic.
"""
import sys

sys.dont_write_bytecode = True

import base64
import contextlib
import json
import os
from pathlib import Path
import socket
import ssl
import struct
import tempfile
import threading
import urllib.error
import urllib.parse
import urllib.request
from unittest import mock

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
for directory in (str(ROOT), str(HERE)):
    if directory not in sys.path:
        sys.path.insert(0, directory)

from _suite_head import check, declare_suite, finish
import qoder_accounts as A
import qoder_netproxy as N
import qoder_settings as S

FIXTURES = HERE / "fixtures" / "outbound-proxy"
CERTIFICATE = FIXTURES / "probe.invalid-cert.pem"
PUBLIC_TEST_KEY = FIXTURES / "probe.invalid-key.pem"
TARGET = "https://probe.invalid/tls-regression?fixture=public"
USERNAME = "public-fixture-user"
PASSWORD = "public:fixture@/ password"
BASIC_AUTH = "Basic " + base64.b64encode(
    (USERNAME + ":" + PASSWORD).encode("utf-8")).decode("ascii")
BODY = b"offline proxy TLS fixture"
TIMEOUT = 5
DEFAULT_CONTEXT = object()
PROXY_ENV = ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy",
             "ALL_PROXY", "all_proxy", "NO_PROXY", "no_proxy")


class LoopbackOnly:
    """Fail before external DNS or connect, even if a production bypass regresses."""

    def __init__(self):
        self.denied = []
        self.connections = []
        self.lookups = []
        self.stack = contextlib.ExitStack()

    def require_loopback(self, host, operation):
        if isinstance(host, bytes):
            host = host.decode("ascii", "replace")
        if host not in ("127.0.0.1", "::1"):
            self.denied.append((operation, host))
            raise AssertionError("offline suite blocked %s to %r" % (operation, host))

    def __enter__(self):
        original_lookup = socket.getaddrinfo
        original_connect = socket.socket.connect
        original_connect_ex = socket.socket.connect_ex

        def lookup(host, *args, **kwargs):
            self.require_loopback(host, "DNS")
            self.lookups.append(host)
            return original_lookup(host, *args, **kwargs)

        def connect(sock, address):
            self.require_loopback(address[0], "connect")
            self.connections.append(address)
            return original_connect(sock, address)

        def connect_ex(sock, address):
            self.require_loopback(address[0], "connect_ex")
            self.connections.append(address)
            return original_connect_ex(sock, address)

        self.stack.enter_context(mock.patch.object(socket, "getaddrinfo", lookup))
        self.stack.enter_context(mock.patch.object(socket.socket, "connect", connect))
        self.stack.enter_context(mock.patch.object(socket.socket, "connect_ex", connect_ex))
        return self

    def __exit__(self, *exc):
        return self.stack.__exit__(*exc)


def recv_exact(conn, count):
    result = b""
    while len(result) < count:
        chunk = conn.recv(count - len(result))
        if not chunk:
            raise EOFError("fixture peer closed before a complete message")
        result += chunk
    return result


def read_headers(conn):
    result = b""
    while not result.endswith(b"\r\n\r\n"):
        if len(result) >= 65536:
            raise ValueError("fixture request headers exceed 64 KiB")
        result += recv_exact(conn, 1)
    return result


def header_values(raw, name):
    prefix = name.lower() + ":"
    return [line.split(":", 1)[1].strip()
            for line in raw.decode("latin-1").split("\r\n")[1:]
            if line.lower().startswith(prefix)]


class LocalTLSProxy:
    """One real loopback connection: authenticate, tunnel, then become TLS origin."""

    def __init__(self, protocol="http"):
        self.protocol = protocol
        self.connect_request = b""
        self.origin_request = b""
        self.proxy_status = None
        self.auth_ok = None
        self.sni = []
        self.socks_greeting = None
        self.socks_auth = None
        self.socks_target = None
        self.tls_errors = []
        self.errors = []
        self.done = threading.Event()
        self.stopping = threading.Event()
        self.connection = None
        self.context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self.context.load_cert_chain(str(CERTIFICATE), str(PUBLIC_TEST_KEY))
        self.context.set_servername_callback(
            lambda tls, name, context: self.sni.append(name))
        self.listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(1)
        self.listener.settimeout(TIMEOUT)
        self.port = self.listener.getsockname()[1]
        self.thread = threading.Thread(target=self.serve, daemon=True,
                                       name="offline-proxy-tls-fixture")

    def url(self, password=PASSWORD):
        return "%s://%s:%s@127.0.0.1:%d" % (
            self.protocol, urllib.parse.quote(USERNAME, safe=""),
            urllib.parse.quote(password, safe=""), self.port)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.stopping.set()
        self.listener.close()
        if self.connection is not None:
            with contextlib.suppress(OSError):
                self.connection.shutdown(socket.SHUT_RDWR)
            self.connection.close()
        self.thread.join(TIMEOUT + 1)
        if self.thread.is_alive():
            raise RuntimeError("local TLS fixture thread did not terminate")

    def serve(self):
        try:
            conn, _ = self.listener.accept()
            self.connection = conn
            conn.settimeout(TIMEOUT)
            with conn:
                if self.protocol == "http":
                    self.http_connect(conn)
                else:
                    self.socks_connect(conn)
        except Exception as exc:
            if not self.stopping.is_set():
                self.errors.append(repr(exc))
        finally:
            self.done.set()

    def http_connect(self, conn):
        self.connect_request = read_headers(conn)
        self.auth_ok = header_values(self.connect_request, "proxy-authorization") == [BASIC_AUTH]
        if not self.auth_ok:
            self.proxy_status = 407
            conn.sendall(b"HTTP/1.1 407 Proxy Authentication Required\r\n"
                         b"Proxy-Authenticate: Basic realm=\"public-test-only\"\r\n"
                         b"Content-Length: 0\r\nConnection: close\r\n\r\n")
            return
        self.proxy_status = 200
        conn.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
        self.tls_origin(conn)

    def socks_connect(self, conn):
        version, count = recv_exact(conn, 2)
        methods = tuple(recv_exact(conn, count))
        self.socks_greeting = (version, methods)
        if version != 5 or 2 not in methods:
            conn.sendall(b"\x05\xff")
            return
        conn.sendall(b"\x05\x02")
        auth_version, length = recv_exact(conn, 2)
        user = recv_exact(conn, length).decode("utf-8")
        password = recv_exact(conn, recv_exact(conn, 1)[0]).decode("utf-8")
        self.socks_auth = (auth_version, user, password)
        self.auth_ok = self.socks_auth == (1, USERNAME, PASSWORD)
        conn.sendall(b"\x01\x00" if self.auth_ok else b"\x01\x01")
        if not self.auth_ok:
            return
        version, command, reserved, address_type = recv_exact(conn, 4)
        if address_type != 3:
            raise AssertionError("SOCKS fixture expects proxy-side domain resolution")
        host = recv_exact(conn, recv_exact(conn, 1)[0]).decode("ascii")
        port = struct.unpack("!H", recv_exact(conn, 2))[0]
        self.socks_target = (version, command, reserved, address_type, host, port)
        conn.sendall(b"\x05\x00\x00\x01\x7f\x00\x00\x01\x00\x00")
        self.tls_origin(conn)

    def tls_origin(self, conn):
        try:
            tls = self.context.wrap_socket(conn, server_side=True)
        except ssl.SSLError as exc:
            self.tls_errors.append(exc)
            return
        self.connection = tls
        with tls:
            self.origin_request = read_headers(tls)
            tls.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\n"
                        b"Content-Length: " + str(len(BODY)).encode("ascii")
                        + b"\r\nConnection: close\r\n\r\n" + BODY)


def trusted_context():
    # Deliberately do NOT use _create_unverified_context or CERT_NONE.
    return ssl.create_default_context(cafile=str(CERTIFICATE))


def real_open(proxy_url, context=DEFAULT_CONTEXT, target=TARGET):
    if context is DEFAULT_CONTEXT:
        opener = N.build_opener(proxy_url)
    else:
        opener = N.build_opener(proxy_url, context=context)
    request = urllib.request.Request(target, headers={"X-Public-TLS-Fixture": "origin-only"})
    with opener.open(request, timeout=TIMEOUT) as response:
        return response.status, response.read()


def capture_error(call):
    try:
        call()
    except Exception as exc:
        if isinstance(exc, urllib.error.HTTPError):
            exc.close()
        return exc
    return None


def assert_completed(proxy, label, tls_rejected=False):
    check(label + "：本地代理完成请求", proxy.done.wait(TIMEOUT + 1))
    check(label + "：代理线程无意外异常", not proxy.errors, proxy.errors)
    check(label + "：TLS 拒绝状态符合预期",
          bool(proxy.tls_errors) == tls_rejected, proxy.tls_errors)


def assert_origin(proxy, label):
    check(label + "：TLS SNI 保留目标主机", proxy.sni == ["probe.invalid"], proxy.sni)
    check(label + "：TLS 内使用 origin-form 请求",
          proxy.origin_request.split(b"\r\n", 1)[0]
          == b"GET /tls-regression?fixture=public HTTP/1.1", proxy.origin_request)
    check(label + "：TLS 源站 Host 正确",
          header_values(proxy.origin_request, "host") == ["probe.invalid"], proxy.origin_request)
    check(label + "：Proxy-Authorization 不泄露给 TLS 源站",
          not header_values(proxy.origin_request, "proxy-authorization")
          and USERNAME.encode() not in proxy.origin_request
          and PASSWORD.encode() not in proxy.origin_request, proxy.origin_request)
    check(label + "：普通请求头仍到达 TLS 源站",
          header_values(proxy.origin_request, "x-public-tls-fixture") == ["origin-only"])


def assert_connect(proxy, label, target="probe.invalid:443"):
    parts = proxy.connect_request.split(b"\r\n", 1)[0].split()
    check(label + "：CONNECT 目标为 " + target,
          len(parts) == 3 and parts[:2] == [b"CONNECT", target.encode("ascii")],
          proxy.connect_request)
    check(label + "：CONNECT 收到精确 Basic 认证头",
          header_values(proxy.connect_request, "proxy-authorization") == [BASIC_AUTH],
          proxy.connect_request)
    check(label + "：普通源站头不混入 CONNECT",
          not header_values(proxy.connect_request, "x-public-tls-fixture"))


@contextlib.contextmanager
def clean_proxy_environment():
    with mock.patch.dict(os.environ):
        for key in PROXY_ENV:
            os.environ.pop(key, None)
        yield


@contextlib.contextmanager
def restore_resolver():
    original = A._PROXY_RESOLVER
    try:
        yield
    finally:
        A.set_proxy_resolver(original)
        A.clear_opener_cache()


def test_http_connect():
    context = trusted_context()
    check("显式信任 fixture 仍校验证书和主机名",
          context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname)
    with LocalTLSProxy() as proxy:
        check("HTTP Basic CONNECT 后完成真实 TLS 请求",
              real_open(proxy.url(), context=context) == (200, BODY))
        assert_completed(proxy, "HTTP CONNECT")
        assert_connect(proxy, "HTTP CONNECT")
        assert_origin(proxy, "HTTP CONNECT")


def test_wrong_http_auth():
    with LocalTLSProxy() as proxy:
        error = capture_error(lambda: real_open(proxy.url(password="public-wrong-password"),
                                                context=trusted_context()))
        reason = getattr(error, "reason", error)
        check("CONNECT 密码错误返回 407 而非 TLS/网络假失败",
              (isinstance(error, urllib.error.HTTPError) and error.code == 407)
              or (isinstance(error, urllib.error.URLError) and "407" in str(reason)), repr(error))
        assert_completed(proxy, "CONNECT 错误认证")
        check("CONNECT 错误认证确实被代理拒绝",
              proxy.auth_ok is False and proxy.proxy_status == 407)
        check("CONNECT 407 后不开始 TLS 或源站请求",
              not proxy.sni and not proxy.origin_request)
        check("CONNECT 407 有明确认证错误说明", "407" in N.explain_error(error),
              N.explain_error(error))


def test_default_context_rejects_self_signed():
    with LocalTLSProxy() as proxy:
        # No context argument: this specifically guards production defaults.
        error = capture_error(lambda: real_open(proxy.url()))
        reason = getattr(error, "reason", error)
        check("默认 TLS context 拒绝自签测试证书",
              isinstance(reason, ssl.SSLCertVerificationError), repr(error))
        check("默认 context 的失败是证书不受信任而非主机名/连接错误",
              getattr(reason, "verify_code", None) in (18, 19), repr(reason))
        assert_completed(proxy, "默认 TLS 校验", tls_rejected=True)
        assert_connect(proxy, "默认 TLS 校验")
        check("默认 TLS 拒绝后没有发送任何源站 HTTP 请求", not proxy.origin_request)


def test_hostname_is_checked():
    with LocalTLSProxy() as proxy:
        error = capture_error(lambda: real_open(proxy.url(), context=trusted_context(),
                                                target="https://wrong-name.invalid/"))
        reason = getattr(error, "reason", error)
        check("信任 fixture 不等于关闭 SAN/主机名校验",
              isinstance(reason, ssl.SSLCertVerificationError)
              and getattr(reason, "verify_code", None) == 62, repr(error))
        assert_completed(proxy, "TLS 主机名校验", tls_rejected=True)
        assert_connect(proxy, "TLS 主机名校验", target="wrong-name.invalid:443")
        check("主机名不符时不会发源站 HTTP 请求", not proxy.origin_request)


def test_no_proxy_does_not_bypass():
    for label, wildcard, bypass in (("NO_PROXY=*", True, False),
                                     ("proxy_bypass=True", False, True)):
        with contextlib.ExitStack() as stack:
            stack.enter_context(clean_proxy_environment())
            # Poison environment proxies remain local and must not replace the explicit one.
            os.environ.update({"HTTP_PROXY": "http://127.0.0.1:1",
                               "HTTPS_PROXY": "http://127.0.0.1:1",
                               "ALL_PROXY": "http://127.0.0.1:1"})
            if wildcard:
                os.environ.update({"NO_PROXY": "*", "no_proxy": "*"})
            if bypass:
                stack.enter_context(mock.patch("urllib.request.proxy_bypass", return_value=True))
            with LocalTLSProxy() as proxy:
                check(label + " 不旁路显式 HTTP 代理",
                      real_open(proxy.url(), context=trusted_context()) == (200, BODY))
                assert_completed(proxy, label)
                assert_connect(proxy, label)
                assert_origin(proxy, label)


def test_socks5_tls_auth():
    with LocalTLSProxy(protocol="socks5") as proxy:
        check("SOCKS5 用户名密码认证后完成真实 TLS 请求",
              real_open(proxy.url(), context=trusted_context()) == (200, BODY))
        assert_completed(proxy, "SOCKS5 TLS")
        check("SOCKS5 提供用户名密码认证方式",
              proxy.socks_greeting is not None and proxy.socks_greeting[0] == 5
              and 2 in proxy.socks_greeting[1], proxy.socks_greeting)
        check("SOCKS5 发送解码后的公开测试用户名密码",
              proxy.socks_auth == (1, USERNAME, PASSWORD), proxy.socks_auth)
        check("SOCKS5 将 probe.invalid:443 交给代理解析而非本地 DNS",
              proxy.socks_target == (5, 1, 0, 3, "probe.invalid", 443), proxy.socks_target)
        assert_origin(proxy, "SOCKS5 TLS")


def test_settings_preserve_other_keys():
    with tempfile.TemporaryDirectory(prefix="qd-proxy-tls-settings-") as directory:
        check("不存在 settings 时允许未配置代理", S.outbound_proxy(directory) == "")
        original = {"api_keys": [{"id": "public-fixture", "key": "not-a-real-key"}],
                    "panel_password_hash": "public-test-not-a-valid-password-hash",
                    "auth_disabled": False, "future_option": {"enabled": True, "values": [1, 2]}}
        S.save(directory, original)
        for label, value in (("设置", "http://127.0.0.1:3128"),
                             ("替换", "socks5://127.0.0.1:1080"), ("清空", "")):
            returned = S.set_outbound_proxy(directory, value)
            saved = S.load(directory, strict=True)
            check(label + "代理后读回值正确",
                  returned == value and S.outbound_proxy(directory) == value)
            if value:
                check(label + "仅写入 outbound_proxy", saved.get("outbound_proxy") == value)
            else:
                check("清空代理时移除 outbound_proxy 键", "outbound_proxy" not in saved)
            others = {key: item for key, item in saved.items() if key != "outbound_proxy"}
            check(label + "代理保留所有其它 settings 键和嵌套值", others == original, saved)


def test_corrupt_settings_fail_closed():
    malformed = (("损坏 JSON", b'{"outbound_proxy":'), ("数组", b"[]"),
                 ("null", b"null"), ("布尔值", b"false"),
                 ("字符串", b'"public fixture"'), ("非法 UTF-8", b"\xff\xfe"))
    with tempfile.TemporaryDirectory(prefix="qd-proxy-tls-corrupt-") as directory:
        path = Path(S.settings_path(directory))
        with restore_resolver():
            for label, raw in malformed:
                path.write_bytes(raw)
                read_error = capture_error(lambda: S.outbound_proxy(directory))
                check(label + " settings 读取 fail closed", isinstance(read_error, ValueError),
                      repr(read_error))
                write_error = capture_error(
                    lambda: S.set_outbound_proxy(directory, "http://127.0.0.1:3128"))
                check(label + " settings 保存拒绝覆盖损坏文件",
                      isinstance(write_error, ValueError) and path.read_bytes() == raw,
                      repr(write_error))
                A.set_proxy_resolver(lambda: S.outbound_proxy(directory))
                with mock.patch.object(A, "_proxy_map_from_env", wraps=A._proxy_map_from_env) as fallback:
                    resolver_error = capture_error(A._opener_for_request)
                    check(label + " settings 故障传播为 ProxyError 不回落环境/直连",
                          isinstance(resolver_error, N.ProxyError) and not fallback.called,
                          repr(resolver_error))


def test_resolver_exception_fail_closed():
    with restore_resolver(), clean_proxy_environment():
        for label, environment in (("无环境代理", {}),
                                   ("有环境代理", {"HTTPS_PROXY": "http://127.0.0.1:1"})):
            state = {"failed": False}

            def resolver():
                if state["failed"]:
                    raise OSError("public-fixture-internal-error-do-not-leak")
                return "http://127.0.0.1:3128"

            with mock.patch.dict(os.environ, environment):
                A.set_proxy_resolver(resolver)
                cached = A._opener_for_request()
                check(label + "：预热的缓存是实际 urllib opener",
                      isinstance(cached, urllib.request.OpenerDirector))
                state["failed"] = True
                with mock.patch.object(A, "_proxy_map_from_env", wraps=A._proxy_map_from_env) as fallback:
                    error = capture_error(A._opener_for_request)
                    check(label + "：resolver 异常不能复用旧 opener 或回落环境/直连",
                          isinstance(error, N.ProxyError) and not fallback.called, repr(error))
                check(label + "：resolver 内部异常详情不泄露到代理报错",
                      error is not None and "public-fixture-internal-error" not in str(error),
                      repr(error))


def main():
    declare_suite(__file__, ("qoder_netproxy.py", "qoder_accounts.py", "qoder_settings.py"))
    with LoopbackOnly() as network, clean_proxy_environment():
        for case in (test_http_connect, test_wrong_http_auth,
                     test_default_context_rejects_self_signed, test_hostname_is_checked,
                     test_no_proxy_does_not_bypass, test_socks5_tls_auth,
                     test_settings_preserve_other_keys, test_corrupt_settings_fail_closed,
                     test_resolver_exception_fail_closed):
            try:
                case()
            except Exception as exc:
                check(case.__name__ + " 未完成（异常不能假绿）", False, repr(exc))
        check("所有真实 socket 连接和 DNS 查询仅允许回环地址",
              bool(network.connections) and bool(network.lookups) and not network.denied,
              network.denied)
    finish()


if __name__ == "__main__":
    main()
