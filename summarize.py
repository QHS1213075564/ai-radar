"""汇总层：读 state/new_*.json -> LLM(Gemini) 生成日报/雷达快报 -> 推送。
无 GEMINI_API_KEY 时降级为规则拼接（原始条目列表）。"""
import json
import os
import sys
from datetime import datetime, timezone

import requests

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
from push import push  # noqa: E402

DAILY_PROMPT = """你是 AI 行业分析师，为关注【图片/视频生成、开源生态、NSFW 创作生态】的用户写每日情报。
输入是 JSON 条目列表。输出 Markdown 日报，结构：
1. 【变革分析】挑 1-2 件真正改变格局的事，各写 3-4 句：它改变了谁的生存方式/让什么变便宜/让什么变过时。没有值得写的就写"今日无格局级事件"。
2. 【新品新工具】过去一天第一次出现的产品/模型/工具，每条一行：名称+一句话它能干什么+链接。
3. 【模型更新】开源/闭源分列，每条一行+链接。
4. 【NSFW 专区】Civitai 与 HF 的 NSFW 新动静。
5. 【变现信号】谁开始收费、付费榜动向、创作者激励。
6. 【一句话速览】其余条目每条一句+链接。
要求：忠实于输入，不编造；某板块无内容写"今日无"；总长 600 字内；标题行用日期。"""

RADAR_PROMPT = """输入是 JSON 条目（刚发生的事件）。写一条手机快报：
第一行：⚡ + 最重要的事件一句话概括。
随后每条一行：名称+一句话+链接。总计 200 字内。忠实输入，不编造。"""

def call_gemini(prompt: str, items: list) -> str:
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        return ""
    body = {"contents": [{"parts": [{"text": prompt + "\n\n" + json.dumps(items, ensure_ascii=False)[:60000]}]}]}
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={key}"
    try:
        r = requests.post(url, json=body, timeout=90)
        r.raise_for_status()
        cand = r.json().get("candidates", [])
        parts = cand[0].get("content", {}).get("parts", []) if cand else []
        return "".join(p.get("text", "") for p in parts)
    except Exception as e:
        print(f"[gemini-fail] {e}", file=sys.stderr)
        return ""

def fallback(items: list) -> str:
    lines = []
    for i in items[:40]:
        lines.append(f"- {i['title']}  \n  {i['url']}")
    return "\n".join(lines) if lines else "（本轮无新条目）"

def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "daily"
    with open(os.path.join(ROOT, "state", f"new_{mode}.json"), encoding="utf8") as f:
        items = json.load(f)
    if not items:
        print(f"[summarize:{mode}] nothing new, silent")
        return
    text = call_gemini(DAILY_PROMPT if mode == "daily" else RADAR_PROMPT, items)
    if not text:
        text = fallback(items)
    title = f"AI雷达 {'日报' if mode=='daily' else '快报'} {datetime.now(timezone.utc).strftime('%m-%d')}"
    push(title, text)

if __name__ == "__main__":
    main()
