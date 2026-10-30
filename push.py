"""AI Radar 推送模块：Bark（主，无审查）+ Server酱/PushPlus（备，微信弹窗）
secrets 里配了哪个通道就发哪个；配多个就都发（冗余送达）。"""
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

def push(title: str, body_md: str) -> bool:
    results = [f(title, body_md) for f in (push_bark, push_serverchan, push_pushplus)]
    sent = [r for r in results if r]
    if not sent:
        print("[push] no channel configured (BARK_KEY / SERVERCHAN_KEY / PUSHPLUS_TOKEN)", file=sys.stderr)
    return any(sent)

if __name__ == "__main__":
    import json
    payload = json.load(sys.stdin)
    sys.exit(0 if push(payload["title"], payload["body"]) else 1)
