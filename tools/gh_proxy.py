# -*- coding: utf-8 -*-
"""临时：本地 HTTP CONNECT 代理，绕过 hosts 里 127.0.0.1 的 github 屏蔽。

原理：git 把 CONNECT github.com:443 发到本代理；本代理不用系统 DNS，
而是直连 GitHub 真实 IP（已实测可达），之后纯字节转发 —— TLS 仍是端到端，
git 正常校验 github.com 的证书。不改系统 hosts、不需要管理员权限。

用法：python -X utf8 _tmp_proxy.py [--port 8443]
然后：git -c http.proxy=http://127.0.0.1:8443 ls-remote https://github.com/...
"""
import argparse
import os
import socket
import sys
import threading
import time

# 只映射**确实被 hosts 屏蔽**的名字（解析成 127.0.0.1 的）。
# 未被屏蔽的名字（例如 uploads.github.com → 20.205.243.161）不要写进来，
# 交给系统 DNS —— 写错 IP 会被 GitHub 前置返回 403。
MAP = {
    "github.com": ["140.82.113.4", "140.82.112.4", "20.205.243.166"],
    "www.github.com": ["140.82.113.4", "140.82.112.4"],
    "api.github.com": ["140.82.113.6", "140.82.112.6"],
    "codeload.github.com": ["140.82.113.9", "140.82.112.9"],
    "objects.githubusercontent.com": ["185.199.108.133"],
    "raw.githubusercontent.com": ["185.199.108.133", "185.199.109.133"],
    "camo.githubusercontent.com": ["185.199.108.133"],
    "cloud.githubusercontent.com": ["185.199.108.133"],
}
LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "saves",
                   "_proxy.log")
_lock = threading.Lock()


def log(msg: str) -> None:
    line = f"[{time.strftime('%H:%M:%S')}] {msg}\n"
    with _lock:
        try:
            os.makedirs(os.path.dirname(LOG), exist_ok=True)
            with open(LOG, "a", encoding="utf-8") as f:
                f.write(line)
        except OSError:
            pass


def dial(host: str, port: int) -> socket.socket:
    ips = MAP.get(host.lower())
    if ips:
        last = None
        for ip in ips:
            try:
                s = socket.create_connection((ip, port), timeout=15)
                log(f"dial {host}:{port} -> {ip} OK")
                return s
            except OSError as e:
                last = e
                log(f"dial {host}:{port} -> {ip} 失败: {e}")
        raise OSError(f"所有映射 IP 均失败: {last}")
    s = socket.create_connection((host, port), timeout=15)   # 兜底：系统解析
    log(f"dial {host}:{port} -> 系统解析 OK")
    return s


def pipe(a: socket.socket, b: socket.socket) -> None:
    try:
        while True:
            data = a.recv(65536)
            if not data:
                break
            b.sendall(data)
    except OSError:
        pass
    finally:
        for s in (a, b):
            try:
                s.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass


def handle(conn: socket.socket) -> None:
    try:
        conn.settimeout(20)
        head = b""
        while b"\r\n\r\n" not in head and len(head) < 65536:
            chunk = conn.recv(4096)
            if not chunk:
                return
            head += chunk
        first = head.split(b"\r\n", 1)[0].decode("latin-1")
        parts = first.split()
        if len(parts) < 2 or parts[0].upper() != "CONNECT":
            log(f"非 CONNECT 请求: {first}")
            conn.sendall(b"HTTP/1.1 405 Method Not Allowed\r\n\r\n")
            return
        target = parts[1]
        host, _, port_s = target.rpartition(":")
        port = int(port_s or 443)
        log(f"CONNECT {host}:{port}")
        upstream = dial(host, port)
        conn.sendall(b"HTTP/1.1 200 Connection established\r\n\r\n")
        conn.settimeout(None)
        upstream.settimeout(None)
        t1 = threading.Thread(target=pipe, args=(conn, upstream), daemon=True)
        t2 = threading.Thread(target=pipe, args=(upstream, conn), daemon=True)
        t1.start()
        t2.start()
        t1.join()
        t2.join()
    except Exception as e:                      # noqa: BLE001
        log(f"handle 异常: {type(e).__name__}: {e}")
    finally:
        try:
            conn.close()
        except OSError:
            pass


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8443)
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args()
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((args.host, args.port))
    srv.listen(64)
    log(f"proxy 监听 {args.host}:{args.port}")
    print(f"[proxy] listening on {args.host}:{args.port}", flush=True)
    while True:
        try:
            conn, _addr = srv.accept()
        except OSError:
            break
        threading.Thread(target=handle, args=(conn,), daemon=True).start()
    return 0


if __name__ == "__main__":
    sys.exit(main())
