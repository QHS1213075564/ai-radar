"""汇总层：读 state/new_*.json -> LLM(Gemini) 生成日报/雷达快报 -> 推送。
无 GEMINI_API_KEY 时降级为规则拼接（原始条目列表）。"""
import json
import os
import sys
from datetime import datetime, timedelta, timezone

import requests

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
from push import push  # noqa: E402

DAILY_PROMPT = """你是 AI 行业分析师，为关注【图片/视频生成、开源生态】的用户写每日情报。
输入是 JSON 条目列表（desc 字段是原文简介，可用于判断条目是干什么的）。输出 Markdown 日报，结构：
1. 【变革分析】挑 1-2 件真正改变格局的事，各写 3-4 句：它改变了谁的生存方式/让什么变便宜/让什么变过时。没有值得写的就写"今日无格局级事件"。
2. 【MiniMax H3 动态】MiniMax 视频模型家族（H3 及其变体如 Fast H3/Pike H3 等）的任何新模型、新版本、社区实测反馈都单独列这里——当前开源视频最强，用户重点盯。无动态写"今日无"。
3. 【图片模型动态】近期值得关注的图片生成模型（开源闭源都要），重点写：能力处于什么水平（榜单/对比）、生成模式（文生图/图生图/编辑/可控排版）、能落地在哪些应用场景。有重磅新模型（如榜单前列、重大能力突破）时优先展开写。无动态写"今日无"。
4. 【强力工作流】新出现的强力工作流（tags 含 workflow 的条目 + Reddit/PH 里的工作流帖 + GitHub 上的优质工作流仓库），重点挑能力强的：能持续生成超15秒视频的、循环/多镜头的、NSFW 可用的、基于 MiniMax H3/海螺的、ComfyUI 新玩法。每条写：工作流干什么+基于什么模型/节点+强在哪+链接。无动态写"今日无"。
5. 【有趣项目】GitHub 上今天有意思的项目：有趣的 skill/工具/玩法项目（如给模型生态"破甲"、解锁限制、新奇 hack 这类），或对普通人有变现意义的项目（能拿来接单/赚钱/省事的）。每条 2-3 句：项目是干什么的+有趣在哪或怎么变现+链接。宁缺毋滥，无动态写"今日无"。
6. 【新品新工具】过去一天第一次出现的产品/模型/工具，每条一行：名称+一句话它能干什么（根据 desc 判断用途/画风/基于什么模型）+链接。
7. 【模型更新】开源/闭源分列，每条一行+一句话用途+链接。
8. 【变现信号】谁开始收费、付费榜动向、创作者激励。
9. 【一句话速览】其余条目每条一句+链接。
要求：忠实于输入，不编造；某板块无内容写"今日无"；总长 1500 字内，重点板块（变革分析/H3/图片/工作流/有趣项目）每条至少两句展开，不要只给名字和链接；标题行用日期。"""

NSFW_PROMPT = """输入是 Civitai/HF 的 NSFW 新条目（desc 是作者简介）。每条写一行：
名称 + 一句话说明它是干什么的（LoRA/Checkpoint/工作流？基于什么底模？画风/用途给谁用？）+ 链接。
desc 为空就根据名称推断并标注"(推断)"。忠实输入，不编造，总长 500 字内。"""

# NSFW 条目不进 LLM（国产 API 网关有审核，喂进去会被拒/吞），由代码直接拼接原文
NSFW_SOURCES = {"civitai"}

def is_nsfw(item: dict) -> bool:
    return item.get("source") in NSFW_SOURCES or bool(item.get("nsfw"))

RADAR_PROMPT = """输入是 JSON 条目（刚发生的事件）。写一条手机快报：
第一行：⚡ + 最重要的事件一句话概括。
随后每条一行：名称+一句话+链接。总计 200 字内。忠实输入，不编造。"""

def call_openai_compat(prompt: str, items: list) -> str:
    """OpenAI 兼容接口：SiliconFlow / Moonshot / DeepSeek 等。
    env: LLM_API_KEY（必填）、LLM_BASE_URL（默认 api.deepseek.com）、LLM_MODEL（默认 deepseek-chat）"""
    key = os.environ.get("LLM_API_KEY")
    if not key:
        return ""
    base = os.environ.get("LLM_BASE_URL", "https://api.deepseek.com").rstrip("/")
    model = os.environ.get("LLM_MODEL", "deepseek-chat")
    body = {"model": model, "messages": [{"role": "user", "content": prompt + "\n\n" + json.dumps(items, ensure_ascii=False)[:60000]}]}
    import time
    for attempt in range(3):
        try:
            r = requests.post(f"{base}/chat/completions", json=body, timeout=300,
                              headers={"Authorization": f"Bearer {key}"})
            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"]
        except ValueError:
            # 大 payload 时网关/CDN 偶发返回 200 空白页（非 JSON），重试
            print(f"[llm-compat-retry {attempt+1}] non-JSON resp: {r.text[:80]!r}", file=sys.stderr)
            time.sleep(15)
        except Exception as e:
            print(f"[llm-compat-fail {attempt+1}] {e}", file=sys.stderr)
            time.sleep(15)
    return ""

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
        d = f"  \n  {i['desc']}" if i.get("desc") else ""
        lines.append(f"- {i['title']}{d}  \n  {i['url']}")
    return "\n".join(lines) if lines else "（本轮无新条目）"

def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", nargs="?", default="daily", choices=["daily", "radar"])
    ap.add_argument("--push", action="store_true", help="推送手机；默认只写本地文件")
    ap.add_argument("--out", default=None, help="输出目录（默认 reports/）")
    args = ap.parse_args()
    mode = args.mode
    state_dir = os.environ.get("RADAR_STATE_DIR") or os.path.join(ROOT, "state")
    with open(os.path.join(state_dir, f"new_{mode}.json"), encoding="utf8") as f:
        items = json.load(f)
    if not items:
        print(f"[summarize:{mode}] nothing new, silent")
        return
    nsfw_items = [i for i in items if is_nsfw(i)]
    safe_items = [i for i in items if not is_nsfw(i)]
    p = DAILY_PROMPT if mode == "daily" else RADAR_PROMPT
    if safe_items:
        text = call_gemini(p, safe_items) or call_openai_compat(p, safe_items) or fallback(safe_items)
    else:
        text = "（今日非 NSFW 区无新增）" if nsfw_items else fallback(items)
    if nsfw_items:
        # NSFW 专区也过 LLM 写简介（OpenRouter 透传无审核）；被吞则降级原文拼接
        sec_text = call_openai_compat(NSFW_PROMPT, nsfw_items) if mode == "daily" else ""
        lines = sec_text if sec_text else "\n".join(f"- {i['title']}  \n  {i['url']}" for i in nsfw_items[:15])
        text += "\n【NSFW 专区】\n" + lines
    # 标题日期用北京时间（UTC+8）
    bj = datetime.now(timezone.utc) + timedelta(hours=8)
    title = f"AI雷达 {'日报' if mode=='daily' else '快报'} {bj.strftime('%m-%d')}"
    if args.push:
        push(title, text)
    else:
        out_dir = args.out or os.path.join(ROOT, "reports")
        os.makedirs(out_dir, exist_ok=True)
        fp = os.path.join(out_dir, f"{bj.strftime('%Y-%m-%d')}_{mode}.md")
        with open(fp, "w", encoding="utf8") as f:
            f.write(f"# {title}\n\n{text}\n")
        print(f"[summarize:{mode}] 已写本地 -> {fp}")

if __name__ == "__main__":
    main()
