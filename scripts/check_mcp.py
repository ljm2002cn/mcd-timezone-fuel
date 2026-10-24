#!/usr/bin/env python3
"""
麦麦时差补给官 · MCP 连通性自检脚本

用途：
  1. 验证 mcd-mcp 配置是否正确（Token 有效 / 传输协议兼容 / 服务可达）
  2. 列出当前账号可用的麦当劳 MCP Tool 清单
  3. 可选：执行一次真实调用（--probe <tool>）验证数据通路

依赖：仅标准库 + 环境变量 MCD_MCP_TOKEN

用法：
  export MCD_MCP_TOKEN="你的Token"
  python3 scripts/check_mcp.py                # 连通性 + Tool 清单
  python3 scripts/check_mcp.py --probe now-time-info
  python3 scripts/check_mcp.py --probe query-meals --args '{"storeId":"..."}'

退出码：0 全部通过；1 存在失败项
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

MCP_URL = os.environ.get("MCD_MCP_URL", "https://mcp.mcd.cn")
TOKEN_ENV = "MCD_MCP_TOKEN"
PROTOCOL_VERSION = "2024-11-05"


def _post(payload: dict, token: str, timeout: int = 20) -> dict:
    """向 MCP Server 发送一次 JSON-RPC 请求。"""
    req = urllib.request.Request(
        MCP_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "Authorization": f"Bearer {token}",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8", errors="replace")

    # Streamable HTTP 可能返回 SSE 格式，取第一条 data: 负载
    if raw.lstrip().startswith("event:") or "\ndata:" in raw or raw.startswith("data:"):
        for line in raw.splitlines():
            line = line.strip()
            if line.startswith("data:"):
                return json.loads(line[5:].strip())
        raise RuntimeError("SSE 响应中未找到 data 负载")
    return json.loads(raw)


def check_env(token: str) -> bool:
    print("=" * 60)
    print("[1/3] 环境变量检查")
    print("=" * 60)
    if not token:
        print(f"  ❌ 未检测到环境变量 {TOKEN_ENV}")
        print(f"     请先执行: export {TOKEN_ENV}=\"你的MCP Token\"")
        print("     Token 申请地址: https://open.mcd.cn/mcp")
        return False
    masked = token[:6] + "*" * max(0, len(token) - 10) + token[-4:]
    print(f"  ✅ {TOKEN_ENV} 已设置  (值: {masked})")
    return True


def check_initialize(token: str) -> bool:
    print()
    print("=" * 60)
    print("[2/3] MCP 服务握手 (initialize)")
    print("=" * 60)
    try:
        res = _post(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": "mcd-timezone-fuel-check", "version": "1.0.0"},
                },
            },
            token,
        )
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")[:300]
        print(f"  ❌ HTTP {e.code} — {body}")
        if e.code == 401:
            print("     → Token 无效或已过期，请到 https://open.mcd.cn/mcp 重新申请")
        elif e.code == 403:
            print("     → 请求被拒绝，检查 Token 权限或服务协议是否已同意")
        elif e.code == 429:
            print("     → 触发限流（600 次/分钟），稍后重试")
        return False
    except urllib.error.URLError as e:
        print(f"  ❌ 网络不可达: {e.reason}")
        return False
    except Exception as e:
        print(f"  ❌ 握手失败: {e}")
        return False

    if "error" in res:
        print(f"  ❌ MCP 返回错误: {res['error']}")
        return False

    info = res.get("result", {}).get("serverInfo", {})
    print(f"  ✅ 握手成功  Server: {info.get('name', '?')} v{info.get('version', '?')}")
    return True


def list_tools(token: str) -> bool:
    print()
    print("=" * 60)
    print("[3/3] Tool 清单 (tools/list)")
    print("=" * 60)
    try:
        res = _post({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}, token)
    except Exception as e:
        print(f"  ❌ 获取失败: {e}")
        return False

    tools = res.get("result", {}).get("tools", [])
    if not tools:
        print("  ⚠️  未返回 Tool 列表")
        return False

    # 本 Skill 实际使用的工具
    used = {
        "now-time-info", "query-nearby-stores", "delivery-query-addresses",
        "delivery-create-address", "delivery-query-stores", "query-meals",
        "query-meal-detail", "list-nutrition-foods", "available-coupons",
        "query-my-coupons", "query-store-coupons", "calculate-price",
        "campaign-calendar", "create-order", "query-order", "order-list",
    }
    names = {t.get("name") for t in tools}
    hit = names & used

    print(f"  ✅ 服务共暴露 {len(tools)} 个 Tool；本 Skill 依赖 {len(hit)}/{len(used)} 个已就绪")
    missing = used - names
    if missing:
        print(f"  ⚠️  服务端暂未提供: {', '.join(sorted(missing))}")
    print()
    for t in sorted(tools, key=lambda x: x.get("name", "")):
        mark = "★" if t.get("name") in used else " "
        desc = (t.get("description") or "").replace("\n", " ")[:72]
        print(f"  {mark} {t.get('name','?'):28} {desc}")
    return True


def probe(token: str, tool: str, args: dict) -> bool:
    print()
    print("=" * 60)
    print(f"[额外] 真实调用 {tool}")
    print("=" * 60)
    try:
        res = _post(
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
             "params": {"name": tool, "arguments": args}},
            token,
        )
    except Exception as e:
        print(f"  ❌ 调用失败: {e}")
        return False
    if "error" in res:
        print(f"  ❌ MCP 错误: {res['error']}")
        return False
    content = res.get("result", {}).get("content", [])
    text = "".join(c.get("text", "") for c in content if c.get("type") == "text")
    print("  ✅ 调用成功，返回：")
    try:
        print(json.dumps(json.loads(text), ensure_ascii=False, indent=2)[:1500])
    except Exception:
        print("  " + text[:1500].replace("\n", "\n  "))
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description="麦麦时差补给官 · MCP 连通性自检")
    ap.add_argument("--probe", help="额外执行一次真实工具调用，指定 Tool 名称")
    ap.add_argument("--args", default="{}", help="配合 --probe 使用的参数 JSON")
    args = ap.parse_args()

    token = os.environ.get(TOKEN_ENV, "").strip()
    results = [check_env(token)]
    if token:
        results.append(check_initialize(token))
        results.append(list_tools(token))
        if args.probe:
            try:
                parsed = json.loads(args.args)
            except json.JSONDecodeError as e:
                print(f"\n❌ --args 不是合法 JSON: {e}")
                return 1
            results.append(probe(token, args.probe, parsed))
    else:
        results += [False, False]

    print()
    print("=" * 60)
    ok = all(results)
    print("自检结果:", "✅ 全部通过" if ok else "❌ 存在失败项，请按上方提示处理")
    print("=" * 60)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
