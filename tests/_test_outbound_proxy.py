# -*- coding: utf-8 -*-
"""出口代理（面板设置 / SOCKS5 隧道 / 代理测试）的本地回归测试。

全程不碰外网：起本地假 HTTP 代理和假 SOCKS5 代理，目标域名用 *.invalid
（URL 合法但不会真的解析；代理收到请求后直接返回固定响应）。
"""
import base64
import os
import shutil
import socket
import struct
import sys
import tempfile
import threading
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import qoder_accounts as A
import qoder_netproxy as N
import qoder_settings

FAILED = []
TOTAL = 0


def check(name, ok, detail=""):
    global TOTAL
    TOTAL += 1
    line = ("PASS " if ok else "FAIL ") + name
    if detail and not ok:
        line += "  [" + str(detail) + "]"
    print(line)
    if not ok:
        FAILED.append(name)


def recv_n(conn, count):
    buf = b""
    while len(buf) < count:
        chunk = conn.recv(count - len(buf))
        if not chunk:
            raise EOFError("peer closed")
        buf += chunk
    return buf


def read_until_headers(conn):
    raw = b""
    while b"\r\n\r\n" not in raw:
        chunk = conn.recv(4096)
        if not chunk:
            break
        raw += chunk
    return raw


# ---------------------------------------------------------------------------
# 假 HTTP 代理（可选 Basic 认证）
# ---------------------------------------------------------------------------
def start_http_proxy(auth=None, body=b"via-px!"):
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen(8)
    port = srv.getsockname()[1]
    seen = []

    def serve():
        while True:
            try:
                conn, _ = srv.accept()
            except OSError:
                return
            threading.Thread(target=handle, args=(conn,), daemon=True).start()

    def handle(conn):
        try:
            raw = read_until_headers(conn)
            head = raw.decode("latin-1", "replace")
            seen.append(head)
            if auth is not None:
                got = ""
                for line in head.split("\r\n"):
                    if line.lower().startswith("proxy-authorization:"):
                        got = line.split(":", 1)[1].strip()
                ok = False
                if got.startswith("Basic "):
                    try:
                        ok = base64.b64decode(got[6:]).decode("utf-8") == auth
                    except Exception:
                        ok = False
                if not ok:
                    conn.sendall(b"HTTP/1.1 407 Proxy Authentication Required\r\n"
                                 b"Proxy-Authenticate: Basic realm=\"test\"\r\n"
                                 b"Content-Length: 0\r\nConnection: close\r\n\r\n")
                    return
            conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\n"
                         b"Content-Length: " + str(len(body)).encode()
                         + b"\r\nConnection: close\r\n\r\n" + body)
        except Exception:
            pass
        finally:
            try:
                conn.close()
            except Exception:
                pass

    threading.Thread(target=serve, daemon=True).start()
    return srv, port, seen


# ---------------------------------------------------------------------------
# 假 SOCKS5 代理（可选用户名密码；CONNECT 成功后自己扮演源站）
# ---------------------------------------------------------------------------
def start_socks5_proxy(credentials=None, body=b"via-s5!"):
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen(8)
    port = srv.getsockname()[1]
    seen = []

    def serve():
        while True:
            try:
                conn, _ = srv.accept()
            except OSError:
                return
            threading.Thread(target=handle, args=(conn,), daemon=True).start()

    def handle(conn):
        try:
            ver, nmethods = recv_n(conn, 2)
            methods = recv_n(conn, nmethods)
            seen.append(("greeting", ver, tuple(methods)))
            if credentials is not None:
                if 2 not in methods:
                    conn.sendall(b"\x05\xff")
                    return
                conn.sendall(b"\x05\x02")
                _, ulen = recv_n(conn, 2)
                user = recv_n(conn, ulen).decode("utf-8")
                plen = recv_n(conn, 1)[0]
                password = recv_n(conn, plen).decode("utf-8")
                if (user, password) != credentials:
                    conn.sendall(b"\x01\x01")
                    return
                conn.sendall(b"\x01\x00")
            else:
                conn.sendall(b"\x05\x00")
            _, cmd, _, atyp = recv_n(conn, 4)
            if atyp == 3:
                host = recv_n(conn, recv_n(conn, 1)[0]).decode("utf-8")
            elif atyp == 1:
                host = socket.inet_ntoa(recv_n(conn, 4))
            else:
                host = ""
            target_port = struct.unpack(">H", recv_n(conn, 2))[0]
            seen.append(("connect", cmd, host, target_port))
            conn.sendall(b"\x05\x00\x00\x01\x00\x00\x00\x00\x00\x00")
            request = read_until_headers(conn)
            seen.append(("request", request.split(b"\r\n")[0]))
            conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\n"
                         b"Content-Length: " + str(len(body)).encode()
                         + b"\r\nConnection: close\r\n\r\n" + body)
        except Exception:
            pass
        finally:
            try:
                conn.close()
            except Exception:
                pass

    threading.Thread(target=serve, daemon=True).start()
    return srv, port, seen


def main():
    # 1) 解析 / 规范化 / 掩码 ------------------------------------------------
    check("空串 -> 直连", N.parse_proxy("") is None)
    check("裸 host:port 补 http://",
          N.normalize_proxy("192.168.0.2:7890") == "http://192.168.0.2:7890")
    spec = N.parse_proxy("http://Clash:xxxxxxxxx@192.168.0.2:7890")
    check("HTTP 认证代理解析",
          bool(spec) and spec["user"] == "Clash"
          and spec["password"] == "xxxxxxxxx" and spec["port"] == 7890, spec)
    check("密码打码",
          N.mask_proxy("http://Clash:xxxxxxxxx@192.168.0.2:7890")
          == "http://Clash:***@192.168.0.2:7890",
          N.mask_proxy("http://Clash:xxxxxxxxx@192.168.0.2:7890"))
    try:
        N.normalize_proxy("ftp://x:1")
        check("拒绝不支持的协议", False)
    except ValueError:
        check("拒绝不支持的协议", True)
    check("describe_proxy 空 -> 未设置", N.describe_proxy("")["set"] is False)

    # 2) HTTP 代理（带认证） ------------------------------------------------
    srv, port, seen = start_http_proxy(auth="Clash:secret")
    try:
        opener = N.build_opener("http://Clash:secret@127.0.0.1:%d" % port)
        resp = opener.open(
            urllib.request.Request("http://probe.invalid/ping"), timeout=5)
        body = resp.read()
        check("HTTP 认证代理可用", resp.status == 200 and body == b"via-px!",
              (resp.status, body))
        check("代理收到 Basic 认证头",
              bool(seen) and "Basic " in seen[0] and "probe.invalid" in seen[0],
              seen[:1])
    finally:
        srv.close()

    # 3) HTTP 代理密码错误 -> 407 -------------------------------------------
    srv, port, seen = start_http_proxy(auth="Clash:secret")
    try:
        opener = N.build_opener("http://Clash:wrong@127.0.0.1:%d" % port)
        try:
            opener.open(urllib.request.Request("http://probe.invalid/"), timeout=5)
            check("HTTP 认证失败被识别", False)
        except urllib.error.HTTPError as exc:
            check("HTTP 认证失败被识别", exc.code == 407, exc.code)
        except Exception as exc:  # pragma: no cover - 不同 Python 版本包装不同
            check("HTTP 认证失败被识别", "407" in str(exc), exc)
    finally:
        srv.close()

    # 4) SOCKS5（无认证） ---------------------------------------------------
    srv, port, seen = start_socks5_proxy()
    try:
        opener = N.build_opener("socks5://127.0.0.1:%d" % port)
        resp = opener.open(urllib.request.Request("http://probe.invalid/ping"),
                           timeout=5)
        body = resp.read()
        check("SOCKS5 无认证可用", resp.status == 200 and body == b"via-s5!",
              (resp.status, body))
        connects = [item for item in seen if item[0] == "connect"]
        check("SOCKS5 CONNECT 目标正确",
              bool(connects) and connects[0][2] == "probe.invalid"
              and connects[0][3] == 80, connects)
        check("隧道内是 origin-form 请求",
              any(b"GET /ping" in item[1] for item in seen
                  if item[0] == "request"), seen)
    finally:
        srv.close()

    # 5) SOCKS5（用户名密码） ----------------------------------------------
    srv, port, seen = start_socks5_proxy(credentials=("Clash", "secret"))
    try:
        opener = N.build_opener("socks5://Clash:secret@127.0.0.1:%d" % port)
        resp = opener.open(urllib.request.Request("http://probe.invalid/"),
                           timeout=5)
        check("SOCKS5 用户名密码可用",
              resp.status == 200 and resp.read() == b"via-s5!")
        greeting = [item for item in seen if item[0] == "greeting"]
        check("SOCKS5 提供用户名密码方式",
              bool(greeting) and 2 in greeting[0][2], greeting)
    finally:
        srv.close()

    # 6) SOCKS5 密码错误 ----------------------------------------------------
    srv, port, seen = start_socks5_proxy(credentials=("Clash", "secret"))
    try:
        opener = N.build_opener("socks5://Clash:bad@127.0.0.1:%d" % port)
        try:
            opener.open(urllib.request.Request("http://probe.invalid/"), timeout=5)
            check("SOCKS5 认证失败被识别", False)
        except Exception as exc:
            text = N.explain_error(exc)
            check("SOCKS5 认证失败被识别",
                  ("认证失败" in text) or ("用户名或密码" in text), text)
    finally:
        srv.close()

    # 7) 面板设置优先于环境变量 --------------------------------------------
    srv, port, seen = start_http_proxy(auth="Clash:secret")
    old_http = os.environ.get("HTTP_PROXY")
    old_https = os.environ.get("HTTPS_PROXY")
    try:
        os.environ["HTTP_PROXY"] = "http://127.0.0.1:9"
        os.environ["HTTPS_PROXY"] = "http://127.0.0.1:9"
        A.set_proxy_resolver(lambda: "http://Clash:secret@127.0.0.1:%d" % port)
        resp = A._opener_for_request().open(
            urllib.request.Request("http://probe.invalid/"), timeout=5)
        check("面板代理优先于环境变量",
              resp.status == 200 and resp.read() == b"via-px!")
        A.set_proxy_resolver(lambda: "")
        try:
            A._opener_for_request().open(
                urllib.request.Request("http://probe.invalid/"), timeout=3)
            check("清空面板代理后回落到环境变量（死端口应失败）", False)
        except Exception:
            check("清空面板代理后回落到环境变量（死端口应失败）", True)
    finally:
        A.set_proxy_resolver(None)
        srv.close()
        for key, old in (("HTTP_PROXY", old_http), ("HTTPS_PROXY", old_https)):
            if old is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = old
        A.clear_opener_cache()

    # 8) settings.json 读写 -------------------------------------------------
    tmp = tempfile.mkdtemp()
    try:
        check("默认无代理", qoder_settings.outbound_proxy(tmp) == "")
        qoder_settings.set_outbound_proxy(
            tmp, "http://Clash:secret@192.168.0.2:7890")
        check("代理写入 settings",
              qoder_settings.outbound_proxy(tmp)
              == "http://Clash:secret@192.168.0.2:7890")
        qoder_settings.set_outbound_proxy(tmp, "")
        check("代理清除", qoder_settings.outbound_proxy(tmp) == "")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # 9) proxy_test 成功 / 失败 --------------------------------------------
    srv, port, seen = start_http_proxy(auth="Clash:secret")
    try:
        good = N.proxy_test("http://Clash:secret@127.0.0.1:%d" % port,
                            probe_url="http://probe.invalid/", timeout=5)
        check("proxy_test 成功路径", good["ok"] and good.get("status") == 200,
              good)
        bad = N.proxy_test("http://Clash:secret@127.0.0.1:9",
                          probe_url="http://probe.invalid/", timeout=3)
        check("proxy_test 死端口报错",
              (not bad["ok"]) and bool(bad.get("error")), bad)
    finally:
        srv.close()

    print()
    print("SUMMARY: TOTAL %d checks, %d passed, %d failed, 0 skipped"
          % (TOTAL, TOTAL - len(FAILED), len(FAILED)))
    print("RESULT: %s (exit %d)" % ("RED" if FAILED else "GREEN", bool(FAILED)))
    if FAILED:
        print("FAILED %d:" % len(FAILED))
        for name in FAILED:
            print("  - " + name)
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
