# 🧠 AI Briefing

A self-hosted daily AI news digest. Configure topics, YouTube channels, and a
delivery time in a clean dark web UI, and every day a Claude-curated briefing
is sent to your Telegram.

**Sources:** new YouTube uploads (`yt-dlp`), AI-lab blogs (RSS via `feedparser`),
top Reddit posts, Product Hunt's AI topic page (via `r.jina.ai`), and live web
search including Twitter/X buzz (the **Exa REST API**). Everything is handed to
**Claude (`claude-sonnet-4-6`)**, which curates and formats the briefing before
it's delivered to **Telegram**.

---

## Features

- **Web UI** at `http://localhost:5000` — dark theme, mobile friendly, no
  front-end frameworks (plain HTML/CSS/JS).
  - **Topics:** four built-in topics (New AI models, AI funding & acquisitions,
    Product Hunt AI launches, AI tools & breakthroughs) plus add-your-own.
  - **YouTube Channels:** add/remove, pre-filled with Matt Wolfe, Liam Ottley,
    and Andrej Karpathy.
  - **Reddit Communities:** add/remove subreddits, pre-filled with
    r/MachineLearning, r/LocalLLaMA, r/artificial.
  - **AI Lab Blogs (RSS):** add/remove feeds, pre-filled with OpenAI, Google
    DeepMind, Mistral, and an Anthropic news feed.
  - **Settings:** daily duration (30 min / 1 hr / 2 hr), delivery time, and
    Telegram chat ID.
- **`config.json`** persists all settings.
- **`briefing.py`** gathers sources, curates with Claude, and sends to Telegram.
- **`scheduler.py`** runs the briefing daily at your configured time (APScheduler).
- **"Send test briefing"** button to trigger a run on demand.

---

## Setup

### 1. Python dependencies

```bash
cd ai-briefing
python3 -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 2. System dependencies

- **`curl`** — used to fetch the Product Hunt page (pre-installed on macOS/Linux).
- **Exa API key** — web search per topic uses the [Exa REST API](https://exa.ai).
  Grab a free key at <https://exa.ai> and set `EXA_API_KEY` in `.env` (below).

### 3. Credentials

Your credentials live in `.env` (already created from the template):

```env
ANTHROPIC_API_KEY=your_key_here
TELEGRAM_BOT_TOKEN=8951992228:AAE56_8pdLhf7NHcFnQL0Hx-fAc4a0y4UZ4
TELEGRAM_CHAT_ID=5554982075
EXA_API_KEY=your_exa_key_here        # free key from https://exa.ai
```

> Replace `ANTHROPIC_API_KEY` with your real Anthropic API key. `.env` is
> git-ignored.

---

## Running

### Web UI

```bash
python app.py
```

Open **http://localhost:5000**, configure your topics / channels / settings, and
click **Save settings**. Use **Send test briefing** to deliver one immediately.

### Daily scheduler

In a second terminal (with the virtualenv active):

```bash
python scheduler.py            # runs every day at your configured delivery time
python scheduler.py --now      # send one briefing now, then keep the daily schedule
```

The scheduler re-reads `config.json` every minute, so changing the delivery time
in the UI takes effect without restarting it.

### One-off run from the CLI

```bash
python briefing.py             # generate and send a briefing now
python briefing.py --no-send   # generate and print, but don't send to Telegram
```

---

## How it works

```
config.json ──► briefing.py
                  ├─ yt-dlp        → new videos (last 48h) from your channels
                  ├─ feedparser    → AI lab blog posts (last 48h) from RSS feeds
                  ├─ reddit         → top posts of the day per subreddit
                  ├─ curl + jina   → Product Hunt AI topic page as clean text
                  ├─ Exa REST API  → web search per topic + Twitter/X buzz
                  ├─ Claude        → curate + format (claude-sonnet-4-6)
                  └─ Telegram      → deliver the briefing
```

The chosen **duration** controls depth: 30 min is a tight 5–7 story digest,
1 hr is ~8–10 focused stories, and 2 hr is an in-depth 12–15 story read.

---

## Files

| File | Purpose |
| --- | --- |
| `app.py` | Flask web server + config / run-now API |
| `briefing.py` | Source gathering, Claude curation, Telegram delivery |
| `scheduler.py` | APScheduler daily runner |
| `config_store.py` | Shared `config.json` read/write helpers |
| `config.json` | Saved user settings |
| `templates/index.html` | Web UI markup |
| `static/style.css` | Dark theme styling |
| `static/app.js` | UI logic |
| `.env` | Credentials (git-ignored) |

---

## Notes

- Telegram has a 4096-character message limit; long briefings are split into
  multiple messages automatically.
- Claude formats the briefing using Telegram-supported HTML. If a chunk ever
  fails to parse, it's re-sent as plain text so delivery never silently fails.
- YouTube date filtering does a bounded check of each channel's most recent
  uploads, so a daily run stays quick.
- **Reddit:** the app uses Reddit's JSON API (which includes each post's score).
  Reddit blocks that endpoint from many datacenter/server IPs with a 403, so the
  app automatically falls back to the subreddit's RSS feed (works everywhere, but
  has no score). On a home/residential connection the JSON API usually works.
- **AI Lab Blogs:** Anthropic does not publish an official RSS feed, so the
  pre-filled "Anthropic (news)" entry is a Google News search feed — remove or
  replace it from the UI any time. Twitter/X is covered automatically as an Exa
  web-search query (no UI), since Exa can't crawl x.com directly.
