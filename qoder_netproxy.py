# -*- coding: utf-8 -*-
"""出站代理：解析 / 校验 / SOCKS5 隧道 / 构建 urllib opener。

qoder2api 原来只认环境变量 ``HTTPS_PROXY`` / ``HTTP_PROXY`` / ``ALL_PROXY``，
而 urllib 本身只会 http/https 代理。这里补两件事：

1. **面板里填一串就能用**：支持 ``http://``、``socks5://``、``socks5h://``，
   认证写进 URL（``socks5://user:pass@host:1080``），和 WorkBuddy 的填法一致。
2. **SOCKS5 用纯标准库实现（项目保持零依赖）**：握手 + CONNECT 隧道，
   urllib 通过自定义 HTTP(S)Connection 走这条隧道。

线程安全：opener 构建无共享状态；``socks5_connect`` 每次新建 socket。
"""

import base64
import http.client
import socket
import ssl
import struct
import time
import urllib.error
import urllib.parse
import urllib.request

SUPPORTED_SCHEMES = ("http", "socks5", "socks5h")
DEFAULT_PORTS = {"http": 80, "socks5": 1080, "socks5h": 1080}
# 不能拿站点根路径当健康接口：CN 根路径会返回 503，但业务路径仍可用。
# 无账号 Token 的只读业务请求预期返回 401；只验证网络/TLS，不验证账号。
PROBE_URL = "https://openapi.qoder.com.cn/api/v1/userinfo"


class ProxyError(Exception):
    """代理不可用 / 握手失败 / 认证失败（消息可直接展示给用户）。"""


# ---------------------------------------------------------------------------
# 解析与展示
# ---------------------------------------------------------------------------

def _as_text(value):
    return str(value or "").strip()


def parse_proxy(value):
    """把用户填的代理串解析成 dict；空串返回 None；非法抛 ValueError。

    没写 scheme 时按 ``http://`` 处理（Clash 混合端口的常见写法
    ``192.168.0.2:7890``）。
    """
    raw = _as_text(value)
    if not raw:
        return None
    if any(ord(c) < 32 or ord(c) == 127 for c in raw):
        raise ValueError("代理地址不能包含控制字符")
    if "://" not in raw:
        raw = "http://" + raw
    try:
        parts = urllib.parse.urlsplit(raw)
        host = parts.hostname
    except ValueError:
        raise ValueError("代理地址无法解析，请检查主机名和端口") from None
    scheme = (parts.scheme or "").lower()
    if scheme == "https":
        raise ValueError("暂不支持到代理本身的 TLS；Clash 通常应填写 http://，仍可访问 HTTPS 上游")
    if scheme not in SUPPORTED_SCHEMES:
        raise ValueError("不支持的代理协议（可用：http、socks5、socks5h）")
    if not host or any(c.isspace() for c in host):
        raise ValueError("代理地址缺少或含无效主机名")
    if parts.path not in ("", "/") or parts.query or parts.fragment:
        raise ValueError("代理地址不能带路径、查询或片段；密码特殊字符请做 URL 编码")
    try:
        port = parts.port
    except ValueError:
        raise ValueError("代理端口不是数字")
    if port is None:
        port = DEFAULT_PORTS[scheme]
    port = int(port)
    if not 0 < port < 65536:
        raise ValueError("代理端口超出范围")
    user = urllib.parse.unquote(parts.username or "")
    password = urllib.parse.unquote(parts.password or "")
    if password and not user:
        raise ValueError("代理地址写了密码但没有用户名")
    if ":" in user or any(ord(c) < 32 or ord(c) == 127 for c in user + password):
        raise ValueError("代理用户名不能包含冒号，凭据不能包含控制字符")
    host_in_url = host
    if ":" in host and not host.startswith("["):
        host_in_url = "[" + host + "]"
    auth = ""
    if user:
        auth = urllib.parse.quote(user, safe="")
        if password:
            auth += ":" + urllib.parse.quote(password, safe="")
        auth += "@"
    return {
        "scheme": scheme,
        "host": host,
        "port": port,
        "user": user,
        "password": password,
        "url": "%s://%s%s:%d" % (scheme, auth, host_in_url, port),
    }


def normalize_proxy(value):
    """规范化后的代理串（存 settings.json 用这个）；空 = 未设置。"""
    spec = parse_proxy(value)
    return spec["url"] if spec else ""


def mask_proxy(value):
    """看板展示用：密码打码。非法/空返回空串。"""
    try:
        spec = parse_proxy(value)
    except ValueError:
        return ""
    if not spec:
        return ""
    auth = (spec["user"] + ":***@") if spec["user"] else ""
    host = "[" + spec["host"] + "]" if ":" in spec["host"] else spec["host"]
    return "%s://%s%s:%d" % (spec["scheme"], auth, host, spec["port"])


def describe_proxy(value):
    """看板需要的状态摘要（不含明文密码）。"""
    try:
        spec = parse_proxy(value)
    except ValueError as exc:
        return {"valid": False, "set": False, "error": str(exc)}
    if not spec:
        return {"valid": True, "set": False}
    return {
        "valid": True,
        "set": True,
        "scheme": spec["scheme"],
        "host": spec["host"],
        "port": spec["port"],
        "has_auth": bool(spec["user"]),
        "masked": mask_proxy(spec["url"]),
    }


# ---------------------------------------------------------------------------
# SOCKS5（RFC 1928 / 1929）
# ---------------------------------------------------------------------------

_SOCKS5_ERRORS = {
    1: "代理内部错误",
    2: "代理规则不允许该目标",
    3: "网络不可达",
    4: "目标主机不可达",
    5: "目标拒绝连接",
    6: "TTL 过期",
    7: "代理不支持 CONNECT 命令",
    8: "代理不支持该地址类型",
}


def _recv_exact(sock, count):
    buf = b""
    while len(buf) < count:
        chunk = sock.recv(count - len(buf))
        if not chunk:
            raise ProxyError("SOCKS5 代理提前关闭了连接")
        buf += chunk
    return buf


def socks5_connect(spec, host, port, timeout=30):
    """经 SOCKS5 代理连到 ``host:port``，返回建好的 TCP socket。"""
    sock = socket.create_connection((spec["host"], spec["port"]),
                                    timeout=timeout)
    try:
        sock.settimeout(timeout)
        user = spec.get("user") or ""
        password = spec.get("password") or ""
        # 问候：没有用户名时只提供 0x00（无认证），否则提供 0x00 + 0x02。
        sock.sendall(b"\x05\x02\x00\x02" if user else b"\x05\x01\x00")
        head = _recv_exact(sock, 2)
        if head[0] != 0x05:
            raise ProxyError("对方不是 SOCKS5 代理（版本号 0x%02x）" % head[0])
        method = head[1]
        if method == 0x02:
            if not user:
                raise ProxyError("SOCKS5 代理要求用户名密码")
            ub = user.encode("utf-8")
            pb = password.encode("utf-8")
            if len(ub) > 255 or len(pb) > 255:
                raise ProxyError("SOCKS5 用户名或密码过长")
            sock.sendall(b"\x01" + bytes([len(ub)]) + ub
                         + bytes([len(pb)]) + pb)
            auth = _recv_exact(sock, 2)
            if auth[1] != 0x00:
                raise ProxyError("SOCKS5 认证失败（用户名或密码不对）")
        elif method != 0x00:
            raise ProxyError("SOCKS5 代理拒绝了所有认证方式（0x%02x）" % method)
        try:
            target = host.encode("idna")
        except UnicodeError:
            target = host.encode("utf-8")
        if len(target) > 255:
            raise ProxyError("目标域名过长")
        # CONNECT：ATYP=0x03（域名），让代理解析 DNS（fake-ip / 远程 DNS 场景）。
        sock.sendall(b"\x05\x01\x00\x03" + bytes([len(target)]) + target
                     + struct.pack(">H", int(port)))
        reply = _recv_exact(sock, 4)
        if reply[0] != 0x05:
            raise ProxyError("SOCKS5 响应无效")
        if reply[1] != 0x00:
            raise ProxyError("SOCKS5 连接失败：%s"
                             % _SOCKS5_ERRORS.get(reply[1],
                                                  "错误码 %d" % reply[1]))
        atyp = reply[3]
        if atyp == 0x01:
            _recv_exact(sock, 4 + 2)
        elif atyp == 0x04:
            _recv_exact(sock, 16 + 2)
        elif atyp == 0x03:
            length = _recv_exact(sock, 1)[0]
            _recv_exact(sock, length + 2)
        else:
            raise ProxyError("SOCKS5 返回了未知地址类型 0x%02x" % atyp)
        return sock
    except Exception:
        try:
            sock.close()
        except Exception:
            pass
        raise


def _timeout_or_default(value, default=30.0):
    if isinstance(value, (int, float)) and value and value > 0:
        return float(value)
    return float(default)


class Socks5HTTPConnection(http.client.HTTPConnection):
    """HTTP（明文）目标走 SOCKS5 隧道。"""

    def __init__(self, *args, **kwargs):
        self._socks5 = kwargs.pop("proxy", None)
        self._socks_timeout = _timeout_or_default(kwargs.get("timeout"))
        super(Socks5HTTPConnection, self).__init__(*args, **kwargs)

    def connect(self):
        self.sock = socks5_connect(self._socks5, self.host, self.port,
                                   timeout=self._socks_timeout)


class Socks5HTTPSConnection(http.client.HTTPSConnection):
    """HTTPS 目标走 SOCKS5 隧道（隧道建好后再做 TLS）。"""

    def __init__(self, *args, **kwargs):
        self._socks5 = kwargs.pop("proxy", None)
        self._socks_timeout = _timeout_or_default(kwargs.get("timeout"))
        super(Socks5HTTPSConnection, self).__init__(*args, **kwargs)

    def connect(self):
        raw = socks5_connect(self._socks5, self.host, self.port,
                             timeout=self._socks_timeout)
        context = getattr(self, "_context", None) or ssl.create_default_context()
        try:
            self.sock = context.wrap_socket(raw, server_hostname=self.host)
        except Exception:
            raw.close()
            raise


class _Socks5Handler(urllib.request.HTTPHandler,
                     urllib.request.HTTPSHandler):
    """把 urllib 的 http/https 连接换成「先过 SOCKS5」的实现。"""

    def __init__(self, spec, context=None):
        urllib.request.HTTPHandler.__init__(self)
        urllib.request.HTTPSHandler.__init__(self, context=context)
        self._spec = spec

    def http_open(self, req):
        return self.do_open(
            lambda *a, **k: Socks5HTTPConnection(*a, proxy=self._spec, **k),
            req)

    def https_open(self, req):
        return self.do_open(
            lambda *a, **k: Socks5HTTPSConnection(*a, proxy=self._spec, **k),
            req, context=self._context)


# ---------------------------------------------------------------------------
# opener 与测试
# ---------------------------------------------------------------------------

class _ExplicitProxyHandler(urllib.request.ProxyHandler):
    """面板明确指定的代理不受 NO_PROXY / Windows ProxyOverride 旁路。"""

    def proxy_open(self, req, proxy, type):
        spec = parse_proxy(proxy)
        if spec["user"]:
            token = base64.b64encode(
                (spec["user"] + ":" + spec["password"]).encode("utf-8"))
            req.add_unredirected_header("Proxy-Authorization",
                                        "Basic " + token.decode("ascii"))
        host = spec["host"]
        if ":" in host:
            host = "[" + host + "]"
        req.set_proxy("%s:%d" % (host, spec["port"]), "http")
        return None  # HTTPSHandler 将认证头只用于 CONNECT，不发给目标站。


def build_opener(proxy_url, context=None):
    """按显式代理构建 opener；空串显式直连；TLS 默认严格校验证书。"""
    spec = parse_proxy(proxy_url)
    tls = urllib.request.HTTPSHandler(context=context)
    if spec is None:
        return urllib.request.build_opener(urllib.request.ProxyHandler({}), tls)
    if spec["scheme"] == "http":
        mapping = {"http": spec["url"], "https": spec["url"]}
        return urllib.request.build_opener(_ExplicitProxyHandler(mapping), tls)
    handler = _Socks5Handler(spec, context=context)
    # SOCKS 隧道不能叠加环境变量或注册表里的 HTTP 代理。
    return urllib.request.build_opener(urllib.request.ProxyHandler({}), handler)


def proxy_test(proxy_url, probe_url=PROBE_URL, timeout=8, opener=None):
    """真实探测链路；opener 参数可复用当前环境/直连的实际出口。"""
    started = time.time()
    try:
        if opener is None:
            opener = build_opener(proxy_url)
        request = urllib.request.Request(
            probe_url, method="GET",
            headers={"User-Agent": "qoder2api-proxy-test"})
        with opener.open(request, timeout=timeout) as resp:
            status = getattr(resp, "status", None) or resp.getcode() or 200
            resp.read(0)
        return {"ok": True, "ms": int((time.time() - started) * 1000),
                "status": int(status), "probe": probe_url}
    except urllib.error.HTTPError as exc:
        status = exc.code
        exc.close()
        if status in (401, 403, 404, 405):
            return {"ok": True, "ms": int((time.time() - started) * 1000),
                    "status": status, "probe": probe_url,
                    "message": "探测未携带账号 Token；HTTP %d 表示目标已响应，不是代理密码错误（代理认证失败为 407）" % status}
        return {"ok": False, "ms": int((time.time() - started) * 1000),
                "status": status, "error": explain_error(exc), "probe": probe_url}
    except Exception as exc:
        return {"ok": False, "ms": int((time.time() - started) * 1000),
                "error": explain_error(exc), "probe": probe_url}


def explain_error(exc):
    """把代理链路上的异常翻成一句人能看懂的话。"""
    if isinstance(exc, ProxyError):
        return str(exc)
    if isinstance(exc, urllib.error.HTTPError):
        if exc.code == 407:
            return "代理认证失败（407）：请检查用户名和密码"
        return "探测地址或代理返回 HTTP %d" % exc.code
    if isinstance(exc, urllib.error.URLError):
        return _explain_reason(getattr(exc, "reason", exc))
    if isinstance(exc, (socket.timeout, TimeoutError)):
        return "连接超时：代理没响应（检查地址/端口/网络）"
    return "%s: %s" % (type(exc).__name__, exc)


def _explain_reason(reason):
    if isinstance(reason, ProxyError):
        return str(reason)
    if isinstance(reason, socket.gaierror):
        return "解析不到代理主机（域名写错了？）"
    if isinstance(reason, ConnectionRefusedError):
        return "连接被拒绝：代理端口没在监听"
    if isinstance(reason, (socket.timeout, TimeoutError)):
        return "连接超时：代理没响应（检查地址/端口/网络）"
    if isinstance(reason, ssl.SSLCertVerificationError):
        return "TLS 证书校验失败：请检查证书、系统时间或代理的 HTTPS 解密设置"
    if isinstance(reason, ssl.SSLError):
        return "TLS 握手失败：请检查代理协议、隧道与目标站 TLS 设置"
    text = str(reason)
    if "407" in text:
        return "代理要求认证（407）：请写成 http://user:pass@host:port"
    if "Tunnel connection failed" in text:
        return "代理拒绝建立隧道：%s" % text
    return text


if __name__ == "__main__":
    samples = ("", "192.168.0.2:7890", "http://user:p%40ss@host:8080",
               "socks5://user:pass@192.168.0.2:7890", "socks5h://1.2.3.4:1080",
               "ftp://x:1")
    for sample in samples:
        try:
            print("%-45r -> %-46s | %s"
                  % (sample, normalize_proxy(sample), mask_proxy(sample)))
        except ValueError as exc:
            print("%-45r -> ERROR: %s" % (sample, exc))
