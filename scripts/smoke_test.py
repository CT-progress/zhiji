#!/usr/bin/env python
"""知记 Zhiji 端到端冒烟测试脚本。

用法：
    # 先启动服务
    python -m zhiji web --port 8000 &
    # 另一个终端运行
    python scripts/smoke_test.py

或指定地址：
    python scripts/smoke_test.py --base http://127.0.0.1:8000
"""

import argparse
import json
import sys
import time
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8000"
PASS = 0
FAIL = 0


def check(name: str, ok: bool, detail: str = ""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}  {detail}")


def req(method: str, path: str, body=None, cookie=None):
    url = BASE + path
    data = json.dumps(body).encode() if body else None
    r = urllib.request.Request(url, data=data, method=method)
    r.add_header("Content-Type", "application/json")
    if cookie:
        r.add_header("Cookie", cookie)
    try:
        resp = urllib.request.urlopen(r, timeout=10)
        return resp.status, resp.read().decode(), resp.headers.get("Set-Cookie", "")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(), ""
    except urllib.error.URLError as e:
        return 0, str(e), ""


def main():
    global BASE
    parser = argparse.ArgumentParser(description="Zhiji smoke test")
    parser.add_argument("--base", default=BASE, help="base URL")
    args = parser.parse_args()
    BASE = args.base.rstrip("/")

    print(f"冒烟测试目标: {BASE}\n")

    # 1. Health check
    print("[1/7] 健康检查")
    code, body, _ = req("GET", "/api/health")
    check("GET /api/health -> 200", code == 200, f"got {code}")
    if code == 200:
        data = json.loads(body)
        check("health status=ok", data.get("status") == "ok", body)

    # 2. Auth status (anonymous)
    print("[2/7] 匿名鉴权状态")
    code, body, _ = req("GET", "/api/auth/status")
    check("GET /api/auth/status -> 200", code == 200, f"got {code}")
    if code == 200:
        data = json.loads(body)
        check("anonymous authenticated=false", data.get("authenticated") is False, body)

    # 3. Register
    print("[3/7] 注册测试用户")
    ts = int(time.time())
    username = f"smoke_{ts}"
    code, body, _ = req("POST", "/api/auth/register", {"username": username, "password": "SmokeTest123!"})
    check("POST /api/auth/register -> 200", code == 200, f"got {code}: {body}")
    cookie = ""
    if code == 200:
        cookie = req("POST", "/api/auth/register", {"username": username, "password": "SmokeTest123!"})[2]
        # register may not set cookie, try login
        code2, body2, cookie2 = req("POST", "/api/auth/login", {"username": username, "password": "SmokeTest123!"})
        check("POST /api/auth/login -> 200", code2 == 200, f"got {code2}: {body2}")
        if cookie2:
            cookie = cookie2.split(";")[0]

    # 4. Authenticated endpoints
    print("[4/7] 鉴权后访问 /api/auth/me")
    code, body, _ = req("GET", "/api/auth/me", cookie=cookie)
    check("GET /api/auth/me -> 200", code == 200, f"got {code}")
    if code == 200:
        data = json.loads(body)
        check("me username matches", data.get("username") == username, body)

    # 5. Models / Platforms
    print("[5/7] 模型与平台列表")
    code, body, _ = req("GET", "/api/models", cookie=cookie)
    check("GET /api/models -> 200", code == 200, f"got {code}")
    if code == 200:
        data = json.loads(body)
        check("models is a list", isinstance(data, list), body)

    code, body, _ = req("GET", "/api/platforms", cookie=cookie)
    check("GET /api/platforms -> 200", code == 200, f"got {code}")

    # 6. Conversations
    print("[6/7] 会话列表")
    code, body, _ = req("GET", "/api/conversations", cookie=cookie)
    check("GET /api/conversations -> 200", code == 200, f"got {code}")

    # 7. Logout
    print("[7/7] 登出")
    code, body, _ = req("POST", "/api/auth/logout", cookie=cookie)
    check("POST /api/auth/logout -> 200", code == 200, f"got {code}")

    print(f"\n{'='*40}")
    print(f"结果: {PASS} passed, {FAIL} failed")
    if FAIL > 0:
        print("冒烟测试失败！")
        sys.exit(1)
    else:
        print("冒烟测试全部通过！")
        sys.exit(0)


if __name__ == "__main__":
    main()
