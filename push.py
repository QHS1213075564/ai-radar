"""AI Radar 推送模块：Bark（主，无审查）→ ggsuper 国内备 → Server酱/PushPlus（微信弹窗）
顺序执行，任一通道成功即停（ggsuper 配额紧，只做备胎不抢主通道消息）。"""
import os
import sys
import requests

def push_bark(title: str, body_md: str) -> bool:
    key = os.environ.get("BARK_KEY")
    if not key:
        return False
    r = requests.post(
        f"https://api.day.app/{key}",
        json={"title": title[:64], "body": body_md[:4096], "group": "ai-radar"},
        timeout=30,
    )
    ok = r.status_code == 200
    print(f"[push/bark] {title} -> {'OK' if ok else r.text[:120]}")
    return ok

def push_serverchan(title: str, body_md: str) -> bool:
    key = os.environ.get("SERVERCHAN_KEY")
    if not key:
        return False
    r = requests.post(
        f"https://sctapi.ftqq.com/{key}.send",
        data={"title": title[:64], "desp": body_md},
        timeout=30,
    )
    j = r.json()
    ok = j.get("code") == 0 or "success" in str(j).lower()
    print(f"[push/serverchan] {title} -> {'OK' if ok else j}")
    return ok

def push_pushplus(title: str, body_md: str) -> bool:
    token = os.environ.get("PUSHPLUS_TOKEN")
    if not token:
        return False
    r = requests.post(
        "http://www.pushplus.plus/send",
        json={"token": token, "title": title[:64], "content": body_md, "template": "markdown"},
        timeout=30,
    )
    j = r.json()
    ok = j.get("code") == 200
    print(f"[push/pushplus] {title} -> {'OK' if ok else j}")
    return ok

def push_ggsuper(title: str, body_md: str) -> bool:
    token = os.environ.get("GGSUPER_TOKEN")
    if not token:
        return False
    # 官方 JSON 入口；成功码为字符串 "80000000"
    r = requests.post(
        "https://www.ggsuper.com.cn/push/api/v1/sendMsg3_New.php",
        json={"title": title[:64], "msg": body_md[:1000],
              "token": token, "issecure": 0, "sender": "AI Radar"},
        timeout=30,
    )
    try:
        j = r.json()
    except ValueError:
        j = {"raw": r.text[:120]}
    ok = str(j.get("code")) == "80000000"
    print(f"[push/ggsuper] {title} -> {'OK' if ok else j}")
    return ok

def push(title: str, body_md: str) -> bool:
    # 顺序执行，任一成功即停；未配置的通道跳过
    for f in (push_bark, push_ggsuper, push_serverchan, push_pushplus):
        if os.environ.get({"push_bark": "BARK_KEY", "push_ggsuper": "GGSUPER_TOKEN",
                           "push_serverchan": "SERVERCHAN_KEY",
                           "push_pushplus": "PUSHPLUS_TOKEN"}[f.__name__]) and f(title, body_md):
            return True
    print("[push] no channel succeeded", file=sys.stderr)
    return False

if __name__ == "__main__":
    import json
    payload = json.load(sys.stdin)
    sys.exit(0 if push(payload["title"], payload["body"]) else 1)
