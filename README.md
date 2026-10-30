# AI Radar — 图片/视频/开源/NSFW 生态情报雷达

电脑关机也照跑的云端雷达：GitHub Actions 定时采集 → LLM 变革分析 → 手机推送（Bark 主通道 + Server酱备通道，配了哪个发哪个，可多发）。

## 两条腿
- **事件雷达**（radar.yml，约每 4–6 小时）：盯官方源——HF 官方 org 上新、官方仓库新 release、Civitai NSFW 新发布。命中即推，没动静不打扰。
- **每日日报**（daily.yml，北京 12:00）：全量采集 + 变革分析 + 新品浮出 + NSFW 专区 + 变现信号。

## 采集面
监视型：HF 官方 org（Qwen/DeepSeek/智谱/月之暗面/MiniMax/字节Seed/Meta/Google/Mistral/OpenAI/微软/BlackForest/Wan/腾讯）、官方 GitHub 旗舰仓库、Civitai API（新发布/热榜/NSFW）、闭源厂商官方发布（OpenAI/Anthropic/DeepMind/Seed/Midjourney，RSS 优先）。
发现型：GitHub Trending、HF Daily Papers、Product Hunt、HN、Reddit（LocalLLaMA/StableDiffusion/SillyTavern/CharacterAI/ComfyUI）、机器之心/量子位 RSS。

## 需要配的 secrets（仓库 Settings → Secrets → Actions）
| Secret | 必填 | 说明 |
|---|---|---|
| `BARK_KEY` | ✅ 主通道 | iOS Bark App（开源推送，无关键词审查，NSFW 安全）里复制的 Key |
| `SERVERCHAN_KEY` 或 `PUSHPLUS_TOKEN` | 建议 | Server酱 sct.ftqq.com 或 pushplus.plus，微信扫码绑定，微信弹窗体感好但腾讯系有词审风险 |
| `GEMINI_API_KEY` | 建议 | aistudio.google.com 免费 key；没有则降级为条目列表 |
| `GITHUB_TOKEN` | 自动 | Actions 内置，无需操作 |

## 本地试跑
```bash
pip install -r requirements.txt
python collect.py --mode daily && python summarize.py daily
```
