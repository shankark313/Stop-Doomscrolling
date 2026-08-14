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

- **Web UI** at `http://localhost:8080` — dark theme, mobile friendly, no
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
- **Built-in scheduler** — an APScheduler job runs inside the web app process and
  delivers the briefing daily at your configured time (no separate process).
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

### Web UI + scheduler (one process)

```bash
python app.py
```

This starts both the web UI and the daily scheduler in a single process. Open
**http://localhost:8080** (override with `PORT=5050 python app.py`), configure
your topics / channels / settings, and click **Save settings**. Use **Send test
briefing** to deliver one immediately.

The built-in scheduler re-reads `config.json` every minute, so changing the
delivery time in the UI takes effect without restarting. Set `FLASK_DEBUG=true`
for local development (auto-reload + debug pages); leave it unset in production.

The delivery time is interpreted in the timezone set by the `TIMEZONE` env var
(IANA name, default `Asia/Kolkata`), independent of the server's own timezone —
so `08:00` means 8am IST even though Railway runs in UTC. Override it with e.g.
`TIMEZONE=America/New_York`.

> **Deploying (e.g. Railway):** the app binds to `$PORT` (default 8080) and runs
> the scheduler in-process, so a single `python app.py` web service is all you
> need — no separate worker.
>
> **Required:** `ANTHROPIC_API_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`,
> `EXA_API_KEY`, and `SERPER_API_KEY` (Reddit does not work without it).
> **Recommended:** `YTDLP_COOKIES_B64` (YouTube does not work from a datacenter
> IP without it), `SUPABASE_URL` + `SUPABASE_ANON_KEY` (settings persistence),
> and `TIMEZONE` if you want something other than IST.
>
> On Railway these go in Project Settings → Shared Variables → Production. A
> shared variable does nothing until you click **SHARE** and link it to the
> service — an unlinked variable is invisible to the app and looks exactly like
> a broken source.
>
> **Settings persistence:** Railway's filesystem is ephemeral, so a plain
> `config.json` loses your settings on every redeploy or restart. Set
> `SUPABASE_URL` and `SUPABASE_ANON_KEY` (free project at
> <https://supabase.com>) and run `supabase_schema.sql` once in the Supabase
> SQL editor — the app then stores settings in the `app_config` table. Without
> these vars it falls back to `config.json`, which is fine locally only.

### One-off run from the CLI

```bash
python briefing.py             # generate and send a briefing now
python briefing.py --no-send   # generate and print, but don't send to Telegram
```

---

## How it works

```
config.json ──► briefing.py
                  ├─ yt-dlp          → new videos (last 48h) from your channels
                  ├─ feedparser      → AI lab blog posts (last 48h) from RSS feeds
                  ├─ Serper (Google) → top posts of the day per subreddit
                  ├─ requests + jina → Product Hunt daily leaderboard as clean text
                  ├─ Exa + Serper    → web search per topic + Twitter/X buzz
                  ├─ Claude          → curate + format (claude-sonnet-4-6)
                  └─ Telegram        → deliver the briefing
```

Channel checks and topic searches run concurrently, so adding channels or topics
costs far less wall-clock time than it used to. A full 9-channel / 25-topic run
takes roughly 2 minutes of gathering plus 1–3 minutes of Claude curation.

The chosen **duration** controls *depth per story*, not how many stories you get:
30 min leads with 5–7 stories, 1 hr with ~8–10, 2 hr with 12–15. Breadth is driven
by your **topic count** instead — every topic with usable material produces at
least one item, so a 25-topic config yields a much longer briefing than a 5-topic
one at the same duration setting. This is deliberate: a fixed story count silently
dropped most of a large topic list.

### Source health

Every run prints a one-line summary of what each source actually returned:

```
SOURCE HEALTH — youtube: 8/9 channels, 10 video(s) | rss: 39 post(s) |
reddit: 13/13 subs, 61 post(s) | producthunt: 3188 chars | topics: 25/25 with results
```

This is the fastest way to spot a silently-broken source. A lane reading `0`
means that source failed, not that there was no news — see
[TROUBLESHOOTING.md](TROUBLESHOOTING.md).

---

## Files

| File | Purpose |
| --- | --- |
| `app.py` | Flask web server + config / run-now API + in-process daily scheduler |
| `briefing.py` | Source gathering, Claude curation, Telegram delivery |
| `config_store.py` | Shared config read/write helpers (Supabase or `config.json`) |
| `TROUBLESHOOTING.md` | Why a source went empty, and the YouTube cookie refresh runbook |
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
- **Reddit needs `SERPER_API_KEY`.** Reddit is effectively unreachable from a
  server: its JSON API answers 403 to non-OAuth clients, and its RSS feed starts
  answering 429 after roughly two subreddits — a cumulative per-IP limit that no
  amount of delay avoids. The app therefore reads Reddit through Google's index
  via serper.dev, which is not rate-limited per subreddit. Reddit's own endpoints
  (JSON, then RSS) remain the fallback when Serper is unconfigured. **Without
  this key the Reddit section will be nearly empty in production.**
- **YouTube needs cookies when deployed.** YouTube serves a "Sign in to confirm
  you're not a bot" interstitial to datacenter IPs, so yt-dlp returns zero videos
  from Railway even though the same channels work fine from a laptop. Set
  `YTDLP_COOKIES_B64` (or `YTDLP_PROXY`) — see
  [TROUBLESHOOTING.md](TROUBLESHOOTING.md) for the export procedure. **These
  cookies expire every few weeks and must be refreshed.**
- **Topic searches use both backends.** Exa does semantic retrieval with a 3-day
  freshness window; Serper's Google News lane adds a hard 24-hour filter and a
  real per-item date. Running both is what keeps theme-phrased topics (e.g.
  "Companies that tried AI and quietly stopped") from coming back empty.
- **AI Lab Blogs:** Anthropic does not publish an official RSS feed, so the
  pre-filled "Anthropic (news)" entry is a Google News search feed — remove or
  replace it from the UI any time. Twitter/X is covered automatically as an Exa
  web-search query (no UI), since Exa can't crawl x.com directly.

---

# 📡 Content Radar (second lane)

`radar.py` is a **separate lane** that shares this project's collectors,
credentials, and Telegram delivery. The two answer different questions:

| | `briefing.py` | `radar.py` |
| --- | --- | --- |
| Question | "what happened in AI today?" | "what should I film today, and can I be early?" |
| Sources | `config.json` — labs, model news, r/MachineLearning | `radar_config.json` — consumer apps, parenting/eldercare, India |
| Search | Exa + Jina | Exa + Jina + **Serper** (Google News 24h, `site:reddit.com`) |
| Model | `claude-sonnet-4-6` | `claude-opus-5`, adaptive thinking, structured JSON output |
| Output | a briefing to read | shoot-ready candidates: hook, the 30-second demo, pillar, format |
| Schedule | 08:00, in-process APScheduler | 07:00, local `launchd` job |

Each candidate is scored 1–5 on **everyday**, **beyond-the-demo**, **India
angle**, and **earliness**, and must clear `beyond-the-demo >= 4` and
`everyday >= 4` — otherwise it lands in `rejected` with a reason, however big
the story. Returning zero candidates on a slow day is a valid result.

```bash
python radar.py             # run, ping Telegram, write the brief
python radar.py --no-send   # generate and print, don't touch Telegram
python radar.py --dry-run   # gather sources only, skip Claude (free)
```

## Output

Two places. Telegram is the notification; the **markdown brief is the working
artifact**:

```
~/Desktop/shankar-brand/content/RADAR-YYYY-MM-DD.md
```

Override the folder with `RADAR_OUTPUT_DIR`. If it doesn't exist the write is
skipped with a log line and the run still delivers to Telegram — **which is why
the radar runs locally, not on Railway**: a cloud filesystem has nowhere to put
the file.

## Not repeating itself

- `radar_seen.json` — every topic the radar has surfaced (last 200), written after each run.
- `content_tracker.csv` in the brand repo — read fresh each run, so anything already shot or queued is off the table.

Both lists go into the prompt as "do not propose anything similar", with an
exact-slug filter after the response as a backstop.

## Scheduling (local, launchd)

```bash
cp com.shankar.contentradar.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.shankar.contentradar.plist
launchctl start com.shankar.contentradar     # run once now to test
tail -f radar.log
```

## Source notes

- **Serper (`serper.dev`)** reaches Google's index and does two jobs nothing
  else here can. **Google News with a 24-hour filter** is the earliness engine —
  the only source with a real per-item timestamp, so "this broke four hours ago"
  is visible. **`site:reddit.com` search** is the Reddit workaround: it reaches
  every sub at once, including ones not in `subreddits` (r/eldercare, r/nri and
  r/daddit have all produced candidates), and Google isn't rate-limiting us.
  ⚠️ The key currently in `.env` is shared with Genopty — mint a separate one at
  <https://serper.dev> if you want the quota split.
- **Reddit direct** is the weak collector but worth keeping for full post text.
  The `.json` API 403s for unauthenticated clients regardless of User-Agent, and
  the RSS feed 429s after the first request or two from a home IP (Jina Reader
  is blocked outright). The radar reads a **rotating window** of
  `subreddits_per_run` subs per day (default 2). Expect 1–2 subs per run; the
  Serper pass above is what actually carries Reddit coverage.
- **Exa** does semantic retrieval and returns page text: five everyday-framed
  queries per run, filtered to the last `freshness_hours` (default 48). It
  cannot crawl reddit.com or x.com — that's why Serper is there.
- **Product Hunt** is the earliest signal on new consumer tools, via the same
  `r.jina.ai` → Exa fallback chain `briefing.py` uses.
- **YouTube** is wired up but `channels` is empty by default — `yt-dlp` is slow
  and no everyday-AI channels are worth polling daily yet. Add handles to
  `radar_config.json` to switch it on.
