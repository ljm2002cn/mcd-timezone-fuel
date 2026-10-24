#!/usr/bin/env python3
"""
麦麦时差补给官 · 真实调用验证与证据采集脚本

用途：
  在拿到真实 MCP Token 后，跑通一条完整的真实数据链路，并把真实返回值
  写进 evidence.json —— 该文件可作为 MCP_INTEGRATION.md 的调用证据，
  也是报名 Issue 中"已线上实测打通"的可信佐证。

链路：
  1. initialize 握手（记录 serverInfo 与工具总数）
  2. now-time-info          → 当前时间（时区换算基准）
  3. query-nearby-stores    → 附近门店（取第一个可用门店）
  4. query-meals            → 该门店在售菜单（记录餐品总数）
  5. list-nutrition-foods   → 营养数据（记录条目数）
  6. available-coupons      → 可领券（记录数量）
  7. calculate-price        → 官方核价（记录实付金额）

用法：
  export MCD_MCP_TOKEN="你的Token"
  python3 scripts/verify_live.py
  python3 scripts/verify_live.py --out ../docs/evidence.json

注意：输出中不含地址、订单号、Token 等敏感信息。
"""

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

MCP_URL = os.environ.get("MCD_MCP_URL", "https://mcp.mcd.cn")
TOKEN_ENV = "MCD_MCP_TOKEN"
PROTOCOL_VERSION = "2024-11-05"

# 本 Skill 依赖的 Tool
USED_TOOLS = [
    "now-time-info", "query-nearby-stores", "query-meals", "query-meal-detail",
    "list-nutrition-foods", "available-coupons", "query-my-coupons",
    "query-store-coupons", "calculate-price", "campaign-calendar",
    "delivery-query-addresses", "delivery-create-address", "delivery-query-stores",
    "create-order", "query-order", "order-list",
]

_rpc_id = 0


def _rpc(token: str, method: str, params: dict | None = None, timeout: int = 25) -> dict:
    """发起一次 JSON-RPC 调用，返回解析后的 JSON。"""
    global _rpc_id
    _rpc_id += 1
    payload = {"jsonrpc": "2.0", "id": _rpc_id, "method": method}
    if params is not None:
        payload["params"] = params

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

    if raw.lstrip().startswith("event:") or raw.startswith("data:") or "\ndata:" in raw:
        for line in raw.splitlines():
            line = line.strip()
            if line.startswith("data:"):
                return json.loads(line[5:].strip())
        raise RuntimeError("SSE 响应未找到 data 负载")
    return json.loads(raw)


def call_tool(token: str, name: str, arguments: dict | None = None) -> dict:
    """调用一个 MCP 工具，返回 {ok, text, raw}。"""
    try:
        res = _rpc(token, "tools/call", {"name": name, "arguments": arguments or {}})
    except urllib.error.HTTPError as e:
        return {"ok": False, "error": f"HTTP {e.code}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}

    if "error" in res:
        return {"ok": False, "error": json.dumps(res["error"], ensure_ascii=False)[:300]}

    content = res.get("result", {}).get("content", [])
    text = "".join(c.get("text", "") for c in content if c.get("type") == "text")
    parsed = None
    try:
        parsed = json.loads(text)
    except Exception:
        pass
    return {"ok": True, "text": text, "json": parsed}


def summarize(name: str, res: dict) -> dict:
    """从工具返回中抽取可公开的摘要（不含地址/订单号/Token）。"""
    out = {"tool": name, "ok": res.get("ok", False)}
    if not res.get("ok"):
        out["error"] = res.get("error", "")
        return out

    data = res.get("json")
    # MCP 常见包装: {"code":0,"msg":"OK","data":{...}}
    payload = data.get("data") if isinstance(data, dict) and "data" in data else data

    if isinstance(payload, list):
        out["item_count"] = len(payload)
    elif isinstance(payload, dict):
        for key in ("list", "items", "records", "stores", "meals", "foods", "coupons", "shops"):
            v = payload.get(key)
            if isinstance(v, list):
                out["item_count"] = len(v)
                out["list_key"] = key
                break
        if "count" in payload and isinstance(payload["count"], int):
            out["item_count"] = payload["count"]
        if "total" in payload and isinstance(payload["total"], int):
            out["item_count"] = payload["total"]

    # now-time-info 提取时间字段
    if name == "now-time-info" and isinstance(payload, dict):
        for k in ("now", "currentTime", "timestamp", "serverTime", "localTime", "dateTime"):
            if k in payload:
                out["time_value"] = str(payload[k])[:60]
                break
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="麦麦时差补给官 · 真实调用验证")
    ap.add_argument("--out", default="../docs/evidence.json", help="证据文件输出路径")
    ap.add_argument("--store", help="指定门店 ID（可选，默认自动取第一个）")
    args = ap.parse_args()

    token = os.environ.get(TOKEN_ENV, "").strip()
    if not token:
        print(f"❌ 未设置 {TOKEN_ENV}")
        print(f"   export {TOKEN_ENV}=\"你的MCP Token\"")
        return 1

    print("=" * 62)
    print("麦麦时差补给官 · 真实调用验证")
    print("=" * 62)

    evidence = {
        "verified_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "server_url": MCP_URL,
        "protocol": "Streamable HTTP",
        "steps": [],
    }

    # Step 1: 握手
    try:
        init = _rpc(token, "initialize", {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "mcd-timezone-fuel", "version": "1.0.0"},
        })
        info = init.get("result", {}).get("serverInfo", {})
        tools_res = _rpc(token, "tools/list", {})
        all_tools = [t.get("name") for t in tools_res.get("result", {}).get("tools", [])]
        print(f"✅ initialize 成功 — Server: {info.get('name')} v{info.get('version')}")
        print(f"✅ 服务端暴露 Tool 总数: {len(all_tools)}")
        evidence["server_info"] = {"name": info.get("name"), "version": info.get("version")}
        evidence["total_tools"] = len(all_tools)
        evidence["used_tools_available"] = sorted(set(all_tools) & set(USED_TOOLS))
        evidence["used_tools_missing"] = sorted(set(USED_TOOLS) - set(all_tools))
    except Exception as e:
        print(f"❌ 握手失败: {e}")
        print("   若为 401/403，请检查 Token 与服务协议同意状态")
        return 1

    # Step 2: 依次调用
    steps = [
        ("now-time-info", {}),
        ("campaign-calendar", {}),
        ("list-nutrition-foods", {}),
        ("available-coupons", {}),
        ("query-my-coupons", {}),
        ("query-nearby-stores", {}),
    ]
    store_id = args.store
    menus = None

    for name, params in steps:
        res = call_tool(token, name, params)
        s = summarize(name, res)
        evidence["steps"].append(s)
        flag = "✅" if s.get("ok") else "❌"
        extra = ""
        if s.get("item_count") is not None:
            extra = f"  返回 {s['item_count']} 条"
        if s.get("time_value"):
            extra = f"  {s['time_value']}"
        print(f"{flag} {name:26}{extra}")
        if not s.get("ok"):
            print(f"     → {s.get('error','')[:160]}")

        # 尝试从 query-nearby-stores 提取门店ID，供后续 query-meals 用
        if name == "query-nearby-stores" and res.get("ok") and not store_id:
            data = res.get("json")
            payload = data.get("data") if isinstance(data, dict) and "data" in data else data
            store_id = _extract_store_id(payload)
            if store_id:
                print(f"     → 提取门店ID: {store_id}")

        time.sleep(0.25)  # 远低于 600 次/分钟限流

    # Step 3: 拉门店菜单
    if store_id:
        res = call_tool(token, "query-meals", {"storeId": store_id})
        s = summarize("query-meals", res)
        s["store_id"] = store_id
        evidence["steps"].append(s)
        print(f"{'✅' if s.get('ok') else '❌'} query-meals (store={store_id})")
        if s.get("ok"):
            print(f"     → 返回 {s.get('item_count')} 条餐品")
            menus = res
        else:
            print(f"     → {s.get('error','')[:160]}")
    else:
        print("⚠️  未取得门店 ID，跳过 query-meals（可手动指定 --store）")

    # Step 4: 用菜单里的真实餐品做一次官方核价
    if menus:
        meal = _pick_meal(menus)
        if meal:
            items = [{"code": meal.get("code"), "quantity": 1}]
            res = call_tool(token, "calculate-price", {
                "storeId": store_id,
                "items": items,
                "dineInType": "takeout",
            })
            s = summarize("calculate-price", res)
            s["sample_item_code"] = meal.get("code")
            s["sample_item_name"] = meal.get("name")
            evidence["steps"].append(s)
            flag = "✅" if s.get("ok") else "❌"
            print(f"{flag} calculate-price  样本: {meal.get('name')}")
            if not s.get("ok"):
                print(f"     → {s.get('error','')[:200]}（参数结构可能需按门店调整）")

    # 汇总
    ok_count = sum(1 for s in evidence["steps"] if s.get("ok"))
    evidence["summary"] = {
        "total": len(evidence["steps"]),
        "succeeded": ok_count,
        "failed": len(evidence["steps"]) - ok_count,
    }
    print()
    print("=" * 62)
    print(f"结果: {ok_count}/{len(evidence['steps'])} 步成功")
    print("=" * 62)

    out_path = os.path.abspath(args.out)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(evidence, f, ensure_ascii=False, indent=2)
    print(f"证据已写入: {out_path}")
    print("提示: 该文件不含 Token/地址/订单号，可安全提交仓库。")
    return 0 if ok_count else 1


def _extract_store_id(payload) -> str | None:
    """从门店列表中提取第一个门店 ID。"""
    if not isinstance(payload, dict):
        return None
    for key in ("list", "stores", "items", "records", "shops"):
        arr = payload.get(key)
        if isinstance(arr, list) and arr:
            first = arr[0]
            if isinstance(first, dict):
                for k in ("storeId", "storeCode", "id", "code"):
                    if first.get(k):
                        return str(first[k])
    # 单对象
    for k in ("storeId", "storeCode", "id"):
        if payload.get(k):
            return str(payload[k])
    return None


def _pick_meal(res: dict) -> dict | None:
    """从菜单返回中挑一个带编码的餐品用于核价。"""
    data = res.get("json")
    payload = data.get("data") if isinstance(data, dict) and "data" in data else data
    if not isinstance(payload, dict):
        return None
    for key in ("list", "meals", "items", "categories"):
        arr = payload.get(key)
        if not isinstance(arr, list) or not arr:
            continue
        for item in arr:
            if not isinstance(item, dict):
                continue
            code = item.get("code") or item.get("mealCode") or item.get("itemCode")
            name = item.get("name") or item.get("mealName") or item.get("itemName")
            if code and name:
                return {"code": code, "name": name}
    return None


if __name__ == "__main__":
    sys.exit(main())
