"""AI Radar 采集器：collect.py --mode radar|daily
双轨采集：monitor(监视已知源) + discover(扫增量)。去重靠 state/seen.json。
radar 模式：命中通知规则 -> 立即推送。daily 模式：全部新条目 -> LLM 日报。
"""
import argparse
import hashlib
import json
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

import requests
import yaml

ROOT = os.path.dirname(os.path.abspath(__file__))
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36"}
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")  # Actions 内置，提升速率限制

def gh_headers():
    h = dict(UA)
    if GITHUB_TOKEN:
        h["Authorization"] = f"Bearer {GITHUB_TOKEN}"
    return h

def get(url, params=None, headers=None, timeout=25, retries=2):
    for attempt in range(retries + 1):
        try:
            r = requests.get(url, params=params, headers=headers or UA, timeout=timeout)
            r.raise_for_status()
            return r
        except requests.RequestException as e:
            if attempt == retries:
                print(f"[fetch-fail] {url} {e}", file=sys.stderr)
                return None
            time.sleep(1.5 * (attempt + 1))
    return None

def clean_desc(text, limit=140):
    """HTML/Markdown 简介清洗为纯文本一行。"""
    if not text:
        return ""
    t = re.sub(r"<[^>]+>", " ", text)
    t = re.sub(r"&[a-z#0-9]+;", " ", t, flags=re.I)
    t = re.sub(r"\s+", " ", t).strip()
    return t[:limit]

def item(source, iid, title, url, tags=None, ts=None, desc=""):
    return {"id": f"{source}:{iid}", "source": source, "title": title.strip(),
            "url": url, "tags": tags or [], "ts": ts or datetime.now(timezone.utc).isoformat(),
            "desc": clean_desc(desc)}

# ---------------- monitor 采集器 ----------------

def fetch_hf_orgs(orgs):
    out = []
    for org in orgs:
        r = get(f"https://huggingface.co/api/models", {"author": org, "sort": "createdAt", "limit": 3})
        if r is None: continue
        for m in r.json()[:3]:
            tagline = " | ".join(filter(None, [
                f"pipeline: {m['pipeline_tag']}" if m.get("pipeline_tag") else "",
                ",".join([t for t in (m.get("tags") or []) if ":" not in t][:4]),
            ]))
            out.append(item("hf_org", m["modelId"], f"[{org}] {m['modelId']}",
                            f"https://huggingface.co/{m['modelId']}", tags=["monitor", "hf", "official"],
                            desc=tagline))
        time.sleep(0.3)
    return out

def fetch_gh_org_repos(orgs):
    out = []
    for org in orgs:
        r = get(f"https://api.github.com/orgs/{org}/repos", {"sort": "created", "per_page": 2}, gh_headers())
        if r is None: continue
        for repo in r.json()[:2]:
            out.append(item("gh_repo", repo["id"], f"[{org}] 新仓库 {repo['full_name']}",
                            repo["html_url"], tags=["monitor", "github", "official"]))
        time.sleep(0.3)
    return out

def fetch_gh_releases(repos):
    out = []
    for repo in repos:
        r = get(f"https://api.github.com/repos/{repo}/releases/latest", headers=gh_headers())
        if r is None or r.status_code != 200: continue
        j = r.json()
        out.append(item("gh_release", j["id"], f"{repo} 发布 {j['name'] or j['tag_name']}",
                        j["html_url"], tags=["monitor", "github", "official"]))
        time.sleep(0.3)
    return out

def fetch_civitai(cfg):
    out = []
    base = "https://api.civitai.com/v1/models"
    if cfg.get("new_models"):
        r = get(base, {"limit": 15, "sort": "Newest", "period": "Day"})
        if r:
            for m in r.json().get("items", []):
                out.append(item("civitai", m["id"], f"Civitai 新模型 {m.get('name','?')}",
                                f"https://civitai.com/models/{m['id']}", tags=["civitai"],
                                desc=m.get("description") or ""))
    if cfg.get("nsfw_new"):
        r = get(base, {"limit": 15, "sort": "Newest", "period": "Day", "nsfw": "true"})
        if r:
            for m in r.json().get("items", []):
                out.append(item("civitai", m["id"], f"Civitai NSFW 新发布 {m.get('name','?')}",
                                f"https://civitai.com/models/{m['id']}", tags=["civitai", "nsfw"],
                                desc=m.get("description") or ""))
    if cfg.get("trending"):
        r = get(base, {"limit": 10, "sort": "Hot", "period": "Day"})
        if r:
            for m in r.json().get("items", []):
                out.append(item("civitai", m["id"], f"Civitai 热榜 {m.get('name','?')}",
                                f"https://civitai.com/models/{m['id']}", tags=["civitai", "trending"],
                                desc=m.get("description") or ""))
    return out

def fetch_changelogs(entries):
    out = []
    for e in entries:
        r = get(e["url"], headers={**UA, "Accept": "text/html"})
        if r is None: continue
        h = hashlib.md5(re.sub(r"\s+", " ", r.text).encode("utf8", "ignore")).hexdigest()
        out.append({"id": f"changelog:{e['name']}:{h}", "source": f"changelog:{e['name']}",
                    "title": f"{e['name']} 官方页面内容变化", "url": e["url"],
                    "tags": ["monitor", "changelog", "changed"], "hash": h,
                    "ts": datetime.now(timezone.utc).isoformat()})
    return out

# ---------------- discover 采集器 ----------------

def fetch_github_trending():
    r = get("https://github.com/trending?since=daily", headers={**UA, "Accept": "text/html"})
    if r is None: return []
    out = []
    for m in re.finditer(r'href="/([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)" data-view-component', r.text):
        out.append(item("gh_trending", m.group(1), f"GitHub Trending {m.group(1)}",
                        f"https://github.com/{m.group(1)}", tags=["discover", "github"]))
    return out[:25]

def fetch_hf_papers():
    date = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
    r = get(f"https://huggingface.co/api/daily_papers", {"date": date})
    if r is None: return []
    out = []
    for p in r.json()[:15]:
        pap = p.get("paper", p)
        out.append(item("hf_paper", pap.get("id", "?"), f"HF Paper {pap.get('title','?')[:100]}",
                        f"https://huggingface.co/papers/{pap.get('id','')}", tags=["discover", "paper"]))
    return out

def fetch_producthunt():
    r = get("https://www.producthunt.com/feed?category=undefined", headers=UA)
    if r is None: return []
    out = []
    try:
        root = ET.fromstring(r.text)
        A = "{http://www.w3.org/2005/Atom}"
        for entry in list(root.iter("entry")) + list(root.iter(A + "entry")):
            t = entry.findtext("title") or entry.findtext(A + "title") or ""
            link = ""
            for l in entry.findall("link") + entry.findall(A + "link"):
                link = l.get("href", "")
            if not t: continue
            d = entry.findtext(A + "content") or entry.findtext("description") or ""
            out.append(item("producthunt", hashlib.md5((t+link).encode()).hexdigest()[:12],
                            f"PH 新品 {t}", link, tags=["discover", "producthunt"], desc=d))
    except ET.ParseError:
        pass
    return out[:20]

def fetch_hackernews(cfg):
    kw = [k.lower() for k in cfg.get("keywords", [])]
    minp = cfg.get("min_points", 100)
    r = get("https://hn.algolia.com/api/v1/search", {"tags": "story", "numericFilters": f"points>{minp}", "hitsPerPage": 30})
    if r is None: return []
    out = []
    for h in r.json().get("hits", []):
        t = (h.get("title") or "").lower()
        if any(k in t for k in kw):
            out.append(item("hackernews", h["objectID"], f"HN {h.get('title','')}",
                            h.get("url") or f"https://news.ycombinator.com/item?id={h['objectID']}",
                            tags=["discover", "hn"]))
    return out[:15]

def fetch_reddit(subs):
    # JSON 端点被 Reddit 反爬封锁（403），走 RSS 端点
    out = []
    for sub in set(subs):
        r = get(f"https://www.reddit.com/r/{sub}/hot.rss", headers=UA)
        if r is None: continue
        try:
            root = ET.fromstring(r.text)
        except ET.ParseError:
            continue
        A = "{http://www.w3.org/2005/Atom}"
        for entry in list(root.iter(A + "entry"))[:10]:
            t = entry.findtext(A + "title") or ""
            link = ""
            for l in entry.findall(A + "link"):
                link = l.get("href", "")
            if not t: continue
            d = entry.findtext(A + "content") or entry.findtext("description") or ""
            out.append(item("reddit", hashlib.md5((t + link).encode()).hexdigest()[:12],
                            f"r/{sub}热帖 {t[:100]}", link, tags=["discover", "reddit", sub], desc=d))
        time.sleep(0.5)
    return out

def fetch_rss(urls):
    out = []
    for u in urls:
        r = get(u, headers=UA)
        if r is None: continue
        try:
            root = ET.fromstring(r.text)
        except ET.ParseError:
            continue
        A = "{http://www.w3.org/2005/Atom}"
        for entry in list(root.iter("item"))[:10] + list(root.iter(A + "entry"))[:10]:
            t = entry.findtext("title") or entry.findtext(A + "title") or ""
            link = entry.findtext("link") or ""
            if not link:
                for l in entry.findall(A + "link"):
                    link = l.get("href", "")
            if not t: continue
            d = entry.findtext("description") or entry.findtext(A + "content") or ""
            out.append(item("rss", hashlib.md5((t+link).encode()).hexdigest()[:12], f"{t}", link, tags=["discover", "rss"], desc=d))
    return out

# ---------------- 状态与主流程 ----------------

def load_state():
    p = os.path.join(ROOT, "state", "seen.json")
    if os.path.exists(p):
        with open(p, encoding="utf8") as f: return json.load(f)
    return {"seen": {}, "changelog_hashes": {}, "last_daily": None}

def save_state(state):
    p = os.path.join(ROOT, "state", "seen.json")
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf8") as f: json.dump(state, f, ensure_ascii=False)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["radar", "daily"], required=True)
    args = ap.parse_args()
    cfg = yaml.safe_load(open(os.path.join(ROOT, "sources.yaml"), encoding="utf8"))
    state = load_state()
    seen = state["seen"]

    items = []
    m, d = cfg["monitor"], cfg["discover"]
    if args.mode == "radar":
        items += fetch_hf_orgs(m["huggingface_orgs"])
        items += fetch_gh_org_repos(m["huggingface_orgs"])
        items += fetch_gh_releases(m["github_repos"])
        items += fetch_civitai({"nsfw_new": True})
        watch = cfg["radar"].get("watchlist") or []
        if watch:
            items = [i for i in items if any(w.lower() in i["title"].lower() for w in watch) or "official" in i["tags"]]
    else:
        items += fetch_hf_orgs(m["huggingface_orgs"])
        items += fetch_gh_org_repos(m["huggingface_orgs"])
        items += fetch_gh_releases(m["github_repos"])
        items += fetch_civitai(m["civitai"])
        items += fetch_changelogs(m["changelogs"])
        items += fetch_github_trending()
        items += fetch_hf_papers()
        items += fetch_producthunt()
        items += fetch_hackernews(d["hackernews"])
        items += fetch_reddit(d["subreddits"])
        items += fetch_rss(d["rss"])

    new = []
    for i in items:
        if i["id"] in seen: continue
        seen[i["id"]] = datetime.now(timezone.utc).isoformat()
        new.append(i)

    # changelog 只报哈希变化的
    if args.mode == "daily":
        ch_new, ch_keep = [], state["changelog_hashes"]
        for i in new:
            if i["source"].startswith("changelog:"):
                name = i["source"].split(":", 1)[1]
                prev = ch_keep.get(name)
                ch_keep[name] = i["hash"]
                if prev and prev != i["hash"]:
                    ch_new.append(i)
            else:
                ch_new.append(i)
        new = ch_new
        state["changelog_hashes"] = ch_keep

    os.makedirs(os.path.join(ROOT, "state"), exist_ok=True)
    with open(os.path.join(ROOT, "state", f"new_{args.mode}.json"), "w", encoding="utf8") as f:
        json.dump(new, f, ensure_ascii=False, indent=1)
    print(f"[collect:{args.mode}] {len(new)} new items")
    save_state(state)

if __name__ == "__main__":
    main()
