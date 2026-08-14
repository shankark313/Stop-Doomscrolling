"""AI Briefing generator.

Gathers fresh AI news from configured YouTube channels (yt-dlp), Product Hunt's
daily AI leaderboard (via r.jina.ai), and the web (the Exa REST API), hands it to Claude
(claude-sonnet-4-6) for curation, and delivers the result to Telegram.

Run directly to generate and send a briefing now:

    python briefing.py
"""
import calendar
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone

from concurrent.futures import ThreadPoolExecutor, as_completed

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


def _ytdlp_base_opts():
    """Base yt-dlp options, including any bot-check workarounds from the env.

    YouTube serves a "Sign in to confirm you're not a bot" interstitial to
    datacenter IPs, which is why channel checks succeed on a laptop and return
    nothing from Railway. Two env vars work around it:
      YTDLP_COOKIES  — contents of a Netscape-format cookies.txt export of a
                       logged-in YouTube session (written to a temp file here).
      YTDLP_PROXY    — an http(s)/socks proxy on a residential IP.
    Neither is required; without them we simply use the default client.
    """
    opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "ignoreerrors": True,
        # The android/web_embedded clients are less aggressively bot-checked
        # than the default web client.
        "extractor_args": {"youtube": {"player_client": ["android", "web"]}},
    }
    proxy = (os.getenv("YTDLP_PROXY") or "").strip()
    if proxy:
        opts["proxy"] = proxy
    cookie_file = _ytdlp_cookie_file()
    if cookie_file:
        opts["cookiefile"] = cookie_file
    return opts


_COOKIE_PATH = None


def _ytdlp_cookie_file():
    """Materialise YTDLP_COOKIES into a temp file once per process, if set."""
    global _COOKIE_PATH
    if _COOKIE_PATH is not None:
        return _COOKIE_PATH or None
    raw = os.getenv("YTDLP_COOKIES") or ""
    if not raw.strip():
        _COOKIE_PATH = ""
        return None
    import tempfile

    handle = tempfile.NamedTemporaryFile(
        "w", suffix=".txt", delete=False, encoding="utf-8"
    )
    handle.write(raw.replace("\\n", "\n"))
    handle.close()
    _COOKIE_PATH = handle.name
    return _COOKIE_PATH


def get_recent_videos(channel, hours=48, max_check=6):
    """Return videos uploaded to a channel within the last `hours`."""
    import yt_dlp  # imported lazily so the web UI starts fast

    name = channel.get("name") or channel.get("handle") or "channel"
    url = _channel_videos_url(channel)
    if not url:
        return []

    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    results = []
    failures = []

    try:
        flat_opts = {
            **_ytdlp_base_opts(),
            "extract_flat": "in_playlist",
            "playlistend": max_check,
        }
        with yt_dlp.YoutubeDL(flat_opts) as ydl:
            info = ydl.extract_info(url, download=False)
        entries = [e for e in (info or {}).get("entries") or [] if e]
    except Exception as exc:  # noqa: BLE001 - network/parse failures are non-fatal
        log(f"  yt-dlp could not list videos for {name}: {exc}")
        return []

    if not entries:
        log(f"  yt-dlp listed 0 videos for {name} (bot check or empty channel?).")
        return []

    with yt_dlp.YoutubeDL(_ytdlp_base_opts()) as ydl:
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
            except Exception as exc:  # noqa: BLE001
                failures.append(str(exc).split("\n")[0][:160])
                continue
            if not data:
                failures.append("extractor returned no data")
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
    if failures and not results:
        log(f"  yt-dlp failed on all {len(failures)} video(s) for {name}: {failures[0]}")
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
def _product_hunt_daily_url():
    """Today's Product Hunt daily leaderboard URL (non-zero-padded month/day)."""
    today = datetime.now()
    return (
        "https://www.producthunt.com/leaderboard/daily/"
        f"{today.year}/{today.month}/{today.day}"
    )


def _jina_fetch(target_url):
    """Fetch a URL as clean text through r.jina.ai (with optional JINA_API_KEY).

    Uses requests (not curl) so it works in minimal containers without curl.
    """
    headers = {"User-Agent": "AIBriefingBot/1.0", "Accept": "text/plain"}
    jina_key = os.getenv("JINA_API_KEY")
    if jina_key and jina_key != "your_jina_key_here":
        headers["Authorization"] = f"Bearer {jina_key}"
    try:
        resp = requests.get(
            f"https://r.jina.ai/{target_url}", headers=headers, timeout=30
        )
    except requests.RequestException as exc:
        log(f"  Product Hunt fetch failed: {exc}")
        return ""
    if not resp.ok:
        log(f"  Product Hunt fetch returned {resp.status_code}.")
        return ""
    return (resp.text or "").strip()


def _looks_blocked(text):
    """True if the fetched text is empty or a Cloudflare bot-check / CAPTCHA page."""
    if not text or len(text) < 200:
        return True
    lowered = text.lower()
    return any(
        marker in lowered
        for marker in ("just a moment", "requiring captcha", "enable javascript and cookies")
    )


def _leaderboard_has_launches(text):
    """True only if the leaderboard markdown actually contains ranked launches.

    Today's leaderboard reads "No posts for this date" until end of day (PST), and
    Product Hunt renders the ranked list with JavaScript, so r.jina.ai usually
    captures only nav/footer chrome. Trust the page only if it isn't the empty
    state and has at least one non-footer product link.
    """
    if "no posts for this date" in text.lower():
        return False
    return bool(re.search(r"producthunt\.com/products/[^)?]+\?ref=(?!footer)", text))


def fetch_product_hunt(max_chars=8000):
    """Fetch today's Product Hunt AI launches.

    Primary: today's daily leaderboard via r.jina.ai. In practice that page is
    empty until end of day (PST) and renders its launches with JavaScript that
    r.jina.ai can't read, so we fall back to an Exa search scoped to
    producthunt.com. (A strict "today" date filter on Exa excludes Product Hunt
    product pages, so we scope by domain and let the prompt enforce today-only.)
    """
    text = _jina_fetch(_product_hunt_daily_url())
    if not _looks_blocked(text) and _leaderboard_has_launches(text):
        return text[:max_chars]

    log("  Product Hunt leaderboard empty/blocked; falling back to Exa (producthunt.com).")
    results = exa_search(
        "new AI products launched today on Product Hunt",
        num_results=8,
        include_domains=["producthunt.com"],
    )
    if not results:
        log("  Product Hunt: no usable content from leaderboard or Exa fallback.")
    return results


# --------------------------------------------------------------------------- #
# Exa web search (Exa REST API)
# --------------------------------------------------------------------------- #
EXA_SEARCH_URL = "https://api.exa.ai/search"


def exa_search(query, num_results=5, include_domains=None, start_published_date=None):
    """Search the web with the Exa REST API.

    Reads EXA_API_KEY from the environment. Optionally restrict results to
    `include_domains` (e.g. ["x.com", "twitter.com"]) and to content published on
    or after `start_published_date` (ISO 8601, e.g. "2026-06-29T00:00:00.000Z").
    Returns a plain-text summary of the results (possibly empty). Get a free key
    at https://exa.ai.
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
    if start_published_date:
        payload["startPublishedDate"] = start_published_date

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
# Serper (Google search / news via serper.dev)
# --------------------------------------------------------------------------- #
SERPER_URL = "https://google.serper.dev"

# Google's `tbs` recency filter. Serper passes it straight through.
SERPER_WINDOWS = {"day": "qdr:d", "week": "qdr:w", "month": "qdr:m"}


def serper_search(query, kind="search", num=10, window=None, country=None, lang="en"):
    """Search Google through serper.dev. Returns a plain-text summary (may be empty).

    `kind` is "search" (organic web results — use a `site:` operator to scope to
    one domain) or "news" (Google News, which carries a relative date per item
    and is the better source when earliness matters). `window` is one of
    SERPER_WINDOWS and restricts results to the last day/week/month; `country`
    is a two-letter code (e.g. "in") that geo-scopes the query.

    Complements exa_search rather than replacing it: Exa does semantic retrieval
    and returns page text, while Serper reaches Google's index — which is the
    only way to see sites Exa can't crawl (reddit.com, x.com) and the only
    source of a reliable 24-hour freshness filter.
    """
    api_key = os.getenv("SERPER_API_KEY")
    if not api_key or api_key == "your_serper_key_here":
        log("  SERPER_API_KEY not set — skipping Google search. Get a key at https://serper.dev")
        return ""

    payload = {"q": query, "num": num, "hl": lang}
    if window:
        payload["tbs"] = SERPER_WINDOWS.get(window, window)
    if country:
        payload["gl"] = country

    try:
        resp = requests.post(
            f"{SERPER_URL}/{kind}",
            headers={"X-API-KEY": api_key, "Content-Type": "application/json"},
            json=payload,
            timeout=30,
        )
    except requests.RequestException as exc:
        log(f"  Serper {kind} failed: {exc}")
        return ""

    if not resp.ok:
        log(f"  Serper {kind} returned {resp.status_code}: {resp.text[:200]}")
        return ""

    results = resp.json().get("news" if kind == "news" else "organic", [])
    if not results:
        log(f"  Serper returned no results for: {query[:60]}")
        return ""

    lines = []
    for res in results:
        title = res.get("title") or "(untitled)"
        link = res.get("link") or ""
        # "date" is relative on news ("3 hours ago") and usually absent on organic.
        date = res.get("date")
        snippet = (res.get("snippet") or "").strip().replace("\n", " ")[:300]
        source = res.get("source")
        meta = " · ".join(p for p in (source, date) if p)
        lines.append(f"- {title} ({link})\n  {meta}\n  {snippet}" if meta
                     else f"- {title} ({link})\n  {snippet}")
    return "\n".join(lines)


def search_topic(topic, days=3):
    """Gather fresh results for one configured topic from both search backends.

    Exa does semantic retrieval and returns page text; Serper's Google News lane
    carries a real per-item date and a hard 24-hour freshness filter. Running
    both is what keeps the long-tail topics (the ones phrased as themes rather
    than keywords) from coming back empty.
    """
    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime(
        "%Y-%m-%dT%H:%M:%S.000Z"
    )
    sections = []

    semantic = exa_search(
        f"latest AI news about {topic}", num_results=5, start_published_date=since
    )
    if semantic:
        sections.append(semantic)

    news = serper_search(f"AI {topic}", kind="news", num=6, window="day")
    if news:
        sections.append(news)

    return "\n".join(sections)


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


def fetch_reddit(subreddits, limit=5, delay=1):
    """Fetch the top posts of the day from each subreddit. Fails gracefully per sub.

    Reddit itself is effectively unreachable from a server: the JSON API answers
    403 to non-OAuth clients, and the RSS feed starts answering 429 after roughly
    two subreddits — a cumulative per-IP limit that a longer `delay` does not
    avoid. So when a Serper key is present we read Reddit through Google's index
    instead, which is not rate-limited per subreddit and returns the same threads.

    Reddit's own endpoints (JSON, then RSS) remain the fallback for when Serper
    is unconfigured or a query comes back empty.
    """
    items = []
    for index, sub in enumerate(subreddits):
        name = _clean_subreddit(sub)
        if not name:
            continue
        posts = _fetch_reddit_serper(name, limit)
        if posts is None:
            if index > 0:
                time.sleep(delay)  # be polite to Reddit; avoids 429 rate-limiting
            posts = _fetch_reddit_json(name, limit)
            if posts is None:
                posts = _fetch_reddit_rss(name, limit)
        if not posts:
            log(f"  Reddit r/{name}: no posts retrieved.")
        items.extend(posts)
    return items


def _fetch_reddit_serper(name, limit):
    """Return today's posts for a subreddit via Google (serper.dev).

    Returns None when Serper is unusable (no key, error, or no results) so the
    caller can fall through to Reddit's own endpoints.
    """
    api_key = os.getenv("SERPER_API_KEY")
    if not api_key or api_key == "your_serper_key_here":
        return None

    payload = {"q": f"site:reddit.com/r/{name}", "num": max(limit * 2, 10), "tbs": "qdr:d"}
    try:
        resp = requests.post(
            f"{SERPER_URL}/search",
            headers={"X-API-KEY": api_key, "Content-Type": "application/json"},
            json=payload,
            timeout=30,
        )
    except requests.RequestException as exc:
        log(f"  Reddit r/{name} via Serper failed: {exc}; trying Reddit directly.")
        return None
    if not resp.ok:
        log(f"  Reddit r/{name} via Serper returned {resp.status_code}; trying Reddit directly.")
        return None

    posts = []
    for res in resp.json().get("organic", []):
        link = res.get("link") or ""
        # Skip subreddit landing pages and other-subreddit bleed-through.
        if f"/r/{name.lower()}/comments/" not in link.lower():
            continue
        posts.append(
            {
                "subreddit": name,
                "title": res.get("title"),
                "url": link,
                "score": None,
                "selftext": (res.get("snippet") or "").strip()[:300],
            }
        )
        if len(posts) >= limit:
            break
    return posts or None


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
- For Product Hunt — only include products that launched TODAY. Ignore any \
promoted, sponsored, or featured products from previous days. If you cannot \
confirm a product launched today, skip it.
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

    channels = cfg.get("channels", [])
    log(f"Checking {len(channels)} YouTube channel(s) for new videos...")
    videos = []
    # Each channel costs ~10s of mostly-network time; run them concurrently so a
    # long channel list doesn't stretch the run into minutes.
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(get_recent_videos, ch): ch for ch in channels}
        for future in as_completed(futures):
            ch = futures[future]
            try:
                found = future.result()
            except Exception as exc:  # noqa: BLE001 - one bad channel must not kill the run
                log(f"  {ch.get('name', ch.get('handle'))}: failed ({exc}).")
                continue
            log(f"  {ch.get('name', ch.get('handle'))}: {len(found)} recent video(s).")
            videos.extend(found)

    log("Fetching AI lab blog feeds (RSS)...")
    rss_items = fetch_rss_feeds(cfg.get("rss_feeds", []))
    log(f"  {len(rss_items)} recent post(s) across {len(cfg.get('rss_feeds', []))} feed(s).")

    log("Fetching top Reddit posts...")
    reddit_items = fetch_reddit(cfg.get("subreddits", []))
    log(f"  {len(reddit_items)} post(s) across {len(cfg.get('subreddits', []))} subreddit(s).")

    log("Fetching Product Hunt daily leaderboard...")
    product_hunt = fetch_product_hunt()

    topics = cfg.get("selected_topics", [])
    log(f"Running web searches for {len(topics)} topic(s)...")
    exa_results = {}
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = {pool.submit(search_topic, topic): topic for topic in topics}
        for future in as_completed(futures):
            topic = futures[future]
            try:
                exa_results[topic] = future.result()
            except Exception as exc:  # noqa: BLE001
                log(f"  Search failed for {topic}: {exc}")
                exa_results[topic] = ""
    empty = [t for t in topics if not exa_results.get(t)]
    if empty:
        log(f"  {len(empty)} topic(s) returned nothing: {', '.join(empty[:5])}")

    # Twitter/X coverage runs automatically as a regular Exa web search (no UI).
    # (Exa can't index x.com/twitter.com directly, so this is a topical query that
    # surfaces what the AI community on X is discussing, via articles and recaps.)
    log("Searching Twitter/X buzz via Exa...")
    exa_results["Twitter / X buzz"] = exa_search(
        "what AI researchers and builders are discussing and posting about on "
        "Twitter / X this week"
    )

    # One-line health check per source, so a silently-empty lane is obvious in
    # the deployment logs instead of only showing up as a missing section.
    log(
        "SOURCE HEALTH — youtube: {}/{} channels, {} video(s) | rss: {} post(s) | "
        "reddit: {}/{} subs, {} post(s) | producthunt: {} chars | topics: {}/{} "
        "with results".format(
            len({v["channel"] for v in videos}), len(channels), len(videos),
            len(rss_items),
            len({r["subreddit"] for r in reddit_items}), len(cfg.get("subreddits", [])),
            len(reddit_items),
            len(product_hunt or ""),
            len(topics) - len(empty), len(topics),
        )
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
