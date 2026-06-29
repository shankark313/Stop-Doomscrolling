"""AI Briefing generator.

Gathers fresh AI news from configured YouTube channels (yt-dlp), Product Hunt's
AI topic page (via r.jina.ai), and the web (the Exa REST API), hands it to Claude
(claude-sonnet-4-6) for curation, and delivers the result to Telegram.

Run directly to generate and send a briefing now:

    python briefing.py
"""
import calendar
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone

import requests
from dotenv import load_dotenv

from config_store import load_config

load_dotenv()

MODEL = "claude-sonnet-4-6"

# How the requested daily duration maps to briefing depth.
DURATION_MAP = {
    "30min": {
        "items": "the 5-7 most important stories",
        "style": "a tight, skimmable 2-3 minute read",
    },
    "1hr": {
        "items": "8-10 stories",
        "style": "a focused 5-6 minute read",
    },
    "2hr": {
        "items": "12-15 stories, with extra context and brief analysis",
        "style": "an in-depth 10+ minute read",
    },
}


def log(msg):
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)


# --------------------------------------------------------------------------- #
# YouTube (yt-dlp)
# --------------------------------------------------------------------------- #
def _channel_videos_url(channel):
    handle = (channel.get("handle") or "").strip()
    if not handle:
        return None
    if handle.startswith("http"):
        url = handle.rstrip("/")
        return url if url.endswith("/videos") else url + "/videos"
    if not handle.startswith("@"):
        handle = "@" + handle
    return f"https://www.youtube.com/{handle}/videos"


def get_recent_videos(channel, hours=48, max_check=6):
    """Return videos uploaded to a channel within the last `hours`."""
    import yt_dlp  # imported lazily so the web UI starts fast

    name = channel.get("name") or channel.get("handle") or "channel"
    url = _channel_videos_url(channel)
    if not url:
        return []

    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    results = []

    try:
        flat_opts = {
            "extract_flat": "in_playlist",
            "playlistend": max_check,
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "ignoreerrors": True,
        }
        with yt_dlp.YoutubeDL(flat_opts) as ydl:
            info = ydl.extract_info(url, download=False)
        entries = [e for e in (info or {}).get("entries") or [] if e]
    except Exception as exc:  # noqa: BLE001 - network/parse failures are non-fatal
        log(f"  yt-dlp could not list videos for {name}: {exc}")
        return []

    full_opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "ignoreerrors": True,
    }
    with yt_dlp.YoutubeDL(full_opts) as ydl:
        for entry in entries[:max_check]:
            vid = entry.get("id") or entry.get("url")
            if not vid:
                continue
            watch = (
                vid if str(vid).startswith("http")
                else f"https://www.youtube.com/watch?v={vid}"
            )
            try:
                data = ydl.extract_info(watch, download=False)
            except Exception:  # noqa: BLE001
                continue
            if not data:
                continue

            uploaded = _video_datetime(data)
            if uploaded and uploaded >= cutoff:
                results.append(
                    {
                        "channel": name,
                        "title": data.get("title"),
                        "url": data.get("webpage_url") or watch,
                        "uploaded": uploaded.isoformat(),
                        "description": (data.get("description") or "")[:400],
                    }
                )
    return results


def _video_datetime(data):
    ts = data.get("timestamp")
    if ts:
        return datetime.fromtimestamp(ts, tz=timezone.utc)
    upload_date = data.get("upload_date")
    if upload_date:
        try:
            return datetime.strptime(upload_date, "%Y%m%d").replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    return None


# --------------------------------------------------------------------------- #
# Product Hunt (curl + r.jina.ai)
# --------------------------------------------------------------------------- #
# Product Hunt's daily leaderboard is a Cloudflare-protected JS SPA that r.jina.ai
# cannot read, so the AI topic page is the reliable, AI-specific source.
PRODUCT_HUNT_URL = "https://www.producthunt.com/topics/artificial-intelligence"


def _jina_fetch(target_url):
    """Fetch a URL as clean text through r.jina.ai (with optional JINA_API_KEY)."""
    cmd = ["curl", "-s", "-L"]
    jina_key = os.getenv("JINA_API_KEY")
    if jina_key and jina_key != "your_jina_key_here":
        cmd += ["-H", f"Authorization: Bearer {jina_key}"]
    cmd.append(f"https://r.jina.ai/{target_url}")
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        log(f"  Product Hunt fetch failed: {exc}")
        return ""
    return (result.stdout or "").strip()


def _looks_blocked(text):
    """True if the fetched text is empty or a Cloudflare bot-check / CAPTCHA page."""
    if not text or len(text) < 200:
        return True
    lowered = text.lower()
    return any(
        marker in lowered
        for marker in ("just a moment", "requiring captcha", "enable javascript and cookies")
    )


def fetch_product_hunt(max_chars=6000):
    """Fetch the Product Hunt AI topic page as clean text via r.jina.ai + curl."""
    text = _jina_fetch(PRODUCT_HUNT_URL)
    if _looks_blocked(text):
        log("  Product Hunt returned no usable content.")
        return ""
    return text[:max_chars]


# --------------------------------------------------------------------------- #
# Exa web search (Exa REST API)
# --------------------------------------------------------------------------- #
EXA_SEARCH_URL = "https://api.exa.ai/search"


def exa_search(query, num_results=5, include_domains=None):
    """Search the web with the Exa REST API.

    Reads EXA_API_KEY from the environment. Optionally restrict results to
    `include_domains` (e.g. ["x.com", "twitter.com"]). Returns a plain-text
    summary of the results (possibly empty). Get a free key at https://exa.ai.
    """
    api_key = os.getenv("EXA_API_KEY")
    if not api_key or api_key == "your_exa_key_here":
        log("  EXA_API_KEY not set — skipping web search. Get a free key at https://exa.ai")
        return ""

    payload = {
        "query": query,
        "numResults": num_results,
        "type": "auto",
        "contents": {"text": {"maxCharacters": 500}},
    }
    if include_domains:
        payload["includeDomains"] = include_domains

    try:
        resp = requests.post(
            EXA_SEARCH_URL,
            headers={"x-api-key": api_key, "Content-Type": "application/json"},
            json=payload,
            timeout=60,
        )
    except requests.RequestException as exc:
        log(f"  Exa search failed: {exc}")
        return ""

    if not resp.ok:
        log(f"  Exa search returned {resp.status_code}: {resp.text[:200]}")
        return ""

    results = resp.json().get("results", [])
    if not results:
        log("  Exa returned no results for this query.")
        return ""

    lines = []
    for res in results:
        title = res.get("title") or "(untitled)"
        url = res.get("url") or ""
        snippet = (res.get("text") or "").strip().replace("\n", " ")[:300]
        lines.append(f"- {title} ({url})\n  {snippet}")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Reddit (top posts of the day)
# --------------------------------------------------------------------------- #
# A realistic browser UA — Reddit's RSS endpoint rejects generic bot agents.
_REDDIT_RSS_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)


def _clean_subreddit(sub):
    name = str(sub).strip().lstrip("/")  # handle "/r/foo"
    if name.lower().startswith("r/"):
        name = name[2:]
    return name.strip().strip("/")


def fetch_reddit(subreddits, limit=5):
    """Fetch the top posts of the day from each subreddit. Fails gracefully per sub.

    Tries Reddit's JSON API first (it includes each post's score). Reddit blocks
    that endpoint from many datacenter IPs with a 403, so on failure we fall back
    to the subreddit's RSS feed, which is far more permissive (but carries no score).
    """
    items = []
    for index, sub in enumerate(subreddits):
        name = _clean_subreddit(sub)
        if not name:
            continue
        if index > 0:
            time.sleep(1)  # be polite to Reddit; avoids 429 rate-limiting
        posts = _fetch_reddit_json(name, limit)
        if posts is None:
            posts = _fetch_reddit_rss(name, limit)
        items.extend(posts)
    return items


def _fetch_reddit_json(name, limit):
    """Return posts via Reddit's JSON API, or None if it's unavailable (e.g. 403)."""
    url = f"https://www.reddit.com/r/{name}/top.json?t=day&limit={limit}"
    try:
        resp = requests.get(url, headers={"User-Agent": "AIBriefingBot/1.0"}, timeout=30)
    except requests.RequestException as exc:
        log(f"  Reddit r/{name} JSON failed: {exc}; trying RSS.")
        return None
    if not resp.ok:
        log(f"  Reddit r/{name} JSON returned {resp.status_code}; trying RSS.")
        return None
    try:
        children = resp.json().get("data", {}).get("children", [])
    except ValueError:
        log(f"  Reddit r/{name} returned non-JSON content; trying RSS.")
        return None

    posts = []
    for child in children:
        post = child.get("data", {})
        permalink = post.get("permalink")
        link = f"https://www.reddit.com{permalink}" if permalink else post.get("url", "")
        posts.append(
            {
                "subreddit": name,
                "title": post.get("title"),
                "url": link,
                "score": post.get("score"),
                "selftext": (post.get("selftext") or "")[:300],
            }
        )
    return posts


def _fetch_reddit_rss(name, limit):
    """Fallback: return posts via the subreddit's RSS feed (no score available)."""
    import feedparser  # imported lazily

    url = f"https://www.reddit.com/r/{name}/top/.rss?t=day"
    try:
        resp = requests.get(url, headers={"User-Agent": _REDDIT_RSS_UA}, timeout=30)
    except requests.RequestException as exc:
        log(f"  Reddit r/{name} RSS failed: {exc}")
        return []
    if not resp.ok:
        log(f"  Reddit r/{name} RSS returned {resp.status_code}.")
        return []

    parsed = feedparser.parse(resp.content)
    posts = []
    for entry in parsed.entries[:limit]:
        posts.append(
            {
                "subreddit": name,
                "title": entry.get("title"),
                "url": entry.get("link"),
                "score": None,
                "selftext": _clean_summary(entry)[:300],
            }
        )
    return posts


# --------------------------------------------------------------------------- #
# RSS / Atom feeds (AI lab blogs)
# --------------------------------------------------------------------------- #
def _entry_datetime(entry):
    """Return an entry's published/updated time as an aware UTC datetime, or None."""
    for key in ("published_parsed", "updated_parsed"):
        struct = entry.get(key)
        if struct:
            return datetime.fromtimestamp(calendar.timegm(struct), tz=timezone.utc)
    return None


def _clean_summary(entry):
    """Strip HTML tags and collapse whitespace from a feed entry's summary."""
    raw = entry.get("summary") or entry.get("description") or ""
    text = re.sub(r"<[^>]+>", " ", raw)
    return re.sub(r"\s+", " ", text).strip()


def fetch_rss_feeds(feeds, hours=48, per_feed_limit=8):
    """Fetch posts from the last `hours` from each RSS/Atom feed. Fails gracefully per feed.

    Each feed contributes at most `per_feed_limit` newest in-window posts so a
    high-volume feed (e.g. a news aggregator) can't dominate the briefing.
    """
    import feedparser  # imported lazily

    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    items = []
    for feed in feeds:
        name = feed.get("name") or feed.get("url") or "feed"
        url = feed.get("url")
        if not url:
            continue
        try:
            parsed = feedparser.parse(url)
        except Exception as exc:  # noqa: BLE001 - feedparser rarely raises, but be safe
            log(f"  RSS {name} failed: {exc}")
            continue
        if not parsed.entries:
            log(f"  RSS {name}: no entries (or could not be parsed).")
            continue

        recent = []
        for entry in parsed.entries:
            published = _entry_datetime(entry)
            # Only include posts we can date to within the window.
            if not published or published < cutoff:
                continue
            recent.append((published, entry))

        recent.sort(key=lambda pair: pair[0], reverse=True)
        for published, entry in recent[:per_feed_limit]:
            items.append(
                {
                    "feed": name,
                    "title": entry.get("title"),
                    "link": entry.get("link"),
                    "published": published.isoformat(),
                    "summary": _clean_summary(entry)[:300],
                }
            )
    return items


# --------------------------------------------------------------------------- #
# Claude curation
# --------------------------------------------------------------------------- #
SYSTEM_PROMPT = """You are an expert AI-news curator who writes a daily briefing \
delivered over Telegram. You receive raw, messy source material from several \
source types: YouTube uploads, a scraped Product Hunt page, web-search results \
(including Twitter/X chatter), Reddit community discussions, and official AI-lab \
blog posts (RSS). Your job is to find the genuinely important and interesting AI \
developments and present them clearly.

Rules:
- Curate ruthlessly. Skip noise, duplicates, low-signal items, and anything not \
about AI. Merge stories that cover the same news across different sources.
- Group items under the user's chosen topics where it makes sense.
- Treat official AI-lab blog posts as high-signal, authoritative announcements — \
surface them prominently when present.
- Include a Reddit discussions section for genuinely notable community threads \
(not every post), and weave in Twitter/X chatter where it adds signal.
- For each item: a short bold headline, 1-2 sentences of what happened and why it \
matters, and a source link when one is available.
- Be specific and factual. Do not invent facts, numbers, or links that are not in \
the source material.
- Output ONLY the briefing body — no preamble like "Here is..." and no sign-off.

MANDATORY sections (always include these — they override "curate ruthlessly"):
- 🎥 Worth watching (YouTube): If the YouTube section of the source material lists \
ANY videos, you MUST include this section and list every one of them (channel — \
title — link). Never silently drop a video that was found, even if other stories \
seem more important. (If no videos were found, omit this section.)
- 💡 One Thing To Think About: ALWAYS end the briefing with this exact section \
title, followed by ONE paragraph of 3-4 sentences identifying the single most \
interesting pattern or connection across ALL of today's sources — an insight the \
reader would miss reading each source individually. Include it every single day.

Other suggested sections (use what the material supports): 🚀 New models, \
💰 Funding & acquisitions, 📰 From the labs (official blogs), 🛠️ Tools & \
breakthroughs, 📦 Product Hunt launches, 💬 Reddit & community.

Formatting (Telegram HTML — this is important):
- Use ONLY these tags: <b>, <i>, <a href="URL">text</a>, <code>.
- Do NOT use <h1>, <ul>, <li>, <br>, Markdown, or any other tags.
- Separate sections and items with blank lines; use "•" for bullet points.
- In plain text, escape & as &amp;, < as &lt;, > as &gt;.
- Use a few tasteful emoji as section markers.
- Keep each section self-contained so it survives being split across messages."""


def curate_with_claude(cfg, source_material, videos):
    from anthropic import Anthropic

    client = Anthropic()  # reads ANTHROPIC_API_KEY from the environment

    depth = DURATION_MAP.get(cfg.get("duration", "1hr"), DURATION_MAP["1hr"])
    max_tokens = {"30min": 4000, "1hr": 6000, "2hr": 8000}.get(
        cfg.get("duration", "1hr"), 6000
    )
    today = datetime.now().strftime("%A, %B %-d, %Y")
    topics = cfg.get("selected_topics") or []

    required = []
    if videos:
        required.append(
            f"{len(videos)} new YouTube video(s) were found in the last 48 hours. You "
            'MUST include a "🎥 Worth watching" section that lists EVERY one of them '
            "(channel — title — clickable link). Do not omit any, even if you judge "
            "them lower-signal than other stories. This overrides curating ruthlessly."
        )
    required.append(
        'Finish with a section titled exactly "💡 One Thing To Think About" — ONE '
        "paragraph of 3-4 sentences naming the single most interesting pattern or "
        "connection across ALL of today's sources; an insight the reader would miss "
        "reading each source on its own. This is mandatory every day, even on slow days."
    )
    required_block = "\n".join(f"- {item}" for item in required)

    user_content = f"""Today is {today}.

Write today's AI briefing. Include {depth['items']} — aim for {depth['style']}.

The reader cares about these topics:
{chr(10).join('- ' + t for t in topics) if topics else '- General AI news'}

REQUIRED today (mandatory — do not skip these):
{required_block}

Start with a single bold title line that includes today's date, then the curated \
sections.

=== RAW SOURCE MATERIAL ===
{source_material}
=== END SOURCE MATERIAL ==="""

    resp = client.messages.create(
        model=MODEL,
        max_tokens=max_tokens,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_content}],
    )
    return "".join(b.text for b in resp.content if b.type == "text").strip()


def assemble_source_material(cfg, videos, product_hunt, exa_results, reddit_items, rss_items):
    parts = []

    parts.append("## NEW YOUTUBE VIDEOS (last 48 hours)")
    if videos:
        for v in videos:
            parts.append(
                f"- [{v['channel']}] {v['title']} ({v['url']})\n"
                f"  Uploaded: {v['uploaded']}\n"
                f"  {v['description']}"
            )
    else:
        parts.append("(No new videos found from configured channels.)")

    parts.append("\n## AI LAB BLOG POSTS (RSS, last 48 hours)")
    if rss_items:
        for item in rss_items:
            parts.append(
                f"- [{item['feed']}] {item['title']} ({item.get('link') or ''})\n"
                f"  Published: {item['published']}\n"
                f"  {item['summary']}"
            )
    else:
        parts.append("(No new lab blog posts in the last 48 hours.)")

    parts.append("\n## REDDIT — TOP POSTS OF THE DAY")
    if reddit_items:
        for item in reddit_items:
            score = item.get("score")
            score_str = f" (score {score})" if score is not None else ""
            parts.append(
                f"- [r/{item['subreddit']}] {item['title']}{score_str} "
                f"({item.get('url') or ''})\n"
                f"  {item['selftext']}"
            )
    else:
        parts.append("(No Reddit posts available.)")

    parts.append("\n## PRODUCT HUNT — ARTIFICIAL INTELLIGENCE")
    parts.append(product_hunt or "(No Product Hunt content available.)")

    parts.append("\n## WEB SEARCH RESULTS (Exa) BY TOPIC")
    if exa_results:
        for topic, text in exa_results.items():
            parts.append(f"\n### {topic}")
            parts.append(text or "(No results.)")
    else:
        parts.append("(No web search results available.)")

    return "\n".join(parts)


# --------------------------------------------------------------------------- #
# Telegram delivery
# --------------------------------------------------------------------------- #
def _split_text(text, limit=3800):
    parts, current = [], ""
    for para in text.split("\n\n"):
        if len(current) + len(para) + 2 > limit:
            if current:
                parts.append(current)
                current = ""
            if len(para) > limit:
                for i in range(0, len(para), limit):
                    parts.append(para[i : i + limit])
                continue
        current = f"{current}\n\n{para}" if current else para
    if current:
        parts.append(current)
    return parts or [text]


def send_telegram(text, token, chat_id):
    if not token or not chat_id:
        log("  Telegram token or chat id missing — cannot deliver.")
        return False
    if not text:
        log("  Nothing to deliver (empty briefing).")
        return False

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    ok = True
    for chunk in _split_text(text):
        payload = {
            "chat_id": chat_id,
            "text": chunk,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        resp = requests.post(url, json=payload, timeout=30)
        if not resp.ok:
            # HTML parse errors are common — retry the chunk as plain text.
            log(f"  Telegram HTML send failed ({resp.status_code}); retrying as plain text.")
            resp = requests.post(
                url,
                json={"chat_id": chat_id, "text": chunk, "disable_web_page_preview": True},
                timeout=30,
            )
        if not resp.ok:
            ok = False
            log(f"  Telegram send failed: {resp.status_code} {resp.text[:200]}")
    return ok


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #
def run_briefing(deliver=True):
    """Build the briefing end-to-end. Returns the briefing text."""
    cfg = load_config()
    log("Starting briefing run.")

    log("Checking YouTube channels for new videos...")
    videos = []
    for channel in cfg.get("channels", []):
        found = get_recent_videos(channel)
        log(f"  {channel.get('name', channel.get('handle'))}: {len(found)} recent video(s).")
        videos.extend(found)

    log("Fetching AI lab blog feeds (RSS)...")
    rss_items = fetch_rss_feeds(cfg.get("rss_feeds", []))
    log(f"  {len(rss_items)} recent post(s) across {len(cfg.get('rss_feeds', []))} feed(s).")

    log("Fetching top Reddit posts...")
    reddit_items = fetch_reddit(cfg.get("subreddits", []))
    log(f"  {len(reddit_items)} post(s) across {len(cfg.get('subreddits', []))} subreddit(s).")

    log("Fetching Product Hunt AI topic page...")
    product_hunt = fetch_product_hunt()

    log("Running Exa web searches per topic...")
    exa_results = {}
    for topic in cfg.get("selected_topics", []):
        exa_results[topic] = exa_search(
            f"latest AI news about {topic} in the past 48 hours"
        )
        log(f"  Searched: {topic}")

    # Twitter/X coverage runs automatically as a regular Exa web search (no UI).
    # (Exa can't index x.com/twitter.com directly, so this is a topical query that
    # surfaces what the AI community on X is discussing, via articles and recaps.)
    log("Searching Twitter/X buzz via Exa...")
    exa_results["Twitter / X buzz"] = exa_search(
        "what AI researchers and builders are discussing and posting about on "
        "Twitter / X this week"
    )

    source_material = assemble_source_material(
        cfg, videos, product_hunt, exa_results, reddit_items, rss_items
    )

    log("Asking Claude to curate the briefing...")
    briefing = curate_with_claude(cfg, source_material, videos)
    log(f"Briefing generated ({len(briefing)} chars).")

    if deliver:
        token = os.getenv("TELEGRAM_BOT_TOKEN")
        chat_id = cfg.get("telegram_chat_id") or os.getenv("TELEGRAM_CHAT_ID")
        log("Delivering to Telegram...")
        if send_telegram(briefing, token, chat_id):
            log("Delivered to Telegram.")
        else:
            log("Telegram delivery had errors (see above).")

    return briefing


if __name__ == "__main__":
    deliver = "--no-send" not in sys.argv
    text = run_briefing(deliver=deliver)
    print("\n" + "=" * 60 + "\nBRIEFING\n" + "=" * 60)
    print(text)
