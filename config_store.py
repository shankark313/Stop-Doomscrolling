"""Shared helpers for reading and writing the AI Briefing config.json."""
import json
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")

DEFAULT_CONFIG = {
    "selected_topics": [
        "New AI models",
        "AI funding & acquisitions",
        "Product Hunt AI launches",
        "AI tools & breakthroughs",
    ],
    "custom_topics": [],
    "channels": [
        {"name": "Matt Wolfe", "handle": "@mreflow"},
        {"name": "Liam Ottley", "handle": "@LiamOttley"},
        {"name": "Andrej Karpathy", "handle": "@AndrejKarpathy"},
    ],
    "subreddits": ["MachineLearning", "LocalLLaMA", "artificial"],
    "rss_feeds": [
        {"name": "OpenAI", "url": "https://openai.com/news/rss.xml"},
        {"name": "Google DeepMind", "url": "https://deepmind.google/blog/rss.xml"},
        {"name": "Mistral", "url": "https://mistral.ai/rss.xml"},
        # Anthropic publishes no official RSS feed, so this is a Google News
        # search feed for Anthropic. Remove or replace it from the UI any time.
        {
            "name": "Anthropic (news)",
            "url": "https://news.google.com/rss/search?q=%22Anthropic%22+AI&hl=en-US&gl=US&ceid=US:en",
        },
    ],
    "duration": "1hr",
    "delivery_time": "08:00",
    "telegram_chat_id": "",
}


def load_config():
    """Load config.json, falling back to defaults for any missing keys."""
    if not os.path.exists(CONFIG_PATH):
        save_config(DEFAULT_CONFIG)
        return dict(DEFAULT_CONFIG)
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return dict(DEFAULT_CONFIG)

    merged = dict(DEFAULT_CONFIG)
    merged.update(data or {})
    return merged


def save_config(cfg):
    """Write the config dict to config.json (pretty-printed)."""
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)
    return cfg


def normalize_config(raw):
    """Coerce an incoming config payload into a clean, safe structure."""
    cfg = dict(DEFAULT_CONFIG)

    if isinstance(raw.get("selected_topics"), list):
        cfg["selected_topics"] = _dedupe(raw["selected_topics"])

    if isinstance(raw.get("custom_topics"), list):
        cfg["custom_topics"] = _dedupe(raw["custom_topics"])

    channels = []
    if isinstance(raw.get("channels"), list):
        for ch in raw["channels"]:
            if not isinstance(ch, dict):
                continue
            name = str(ch.get("name", "")).strip()
            handle = str(ch.get("handle", "")).strip()
            if name or handle:
                channels.append({"name": name or handle, "handle": handle})
    cfg["channels"] = channels

    subreddits = []
    if isinstance(raw.get("subreddits"), list):
        for sub in raw["subreddits"]:
            name = str(sub).strip().lstrip("/")  # handle "/r/foo"
            if name.lower().startswith("r/"):
                name = name[2:]
            name = name.strip().strip("/")
            if name:
                subreddits.append(name)
    cfg["subreddits"] = _dedupe(subreddits)

    feeds = []
    if isinstance(raw.get("rss_feeds"), list):
        for feed in raw["rss_feeds"]:
            if not isinstance(feed, dict):
                continue
            name = str(feed.get("name", "")).strip()
            url = str(feed.get("url", "")).strip()
            if url:
                feeds.append({"name": name or url, "url": url})
    cfg["rss_feeds"] = feeds

    duration = str(raw.get("duration", "1hr")).strip()
    cfg["duration"] = duration if duration in ("30min", "1hr", "2hr") else "1hr"

    delivery_time = str(raw.get("delivery_time", "08:00")).strip()
    cfg["delivery_time"] = delivery_time if _valid_time(delivery_time) else "08:00"

    cfg["telegram_chat_id"] = str(raw.get("telegram_chat_id", "")).strip()
    return cfg


def _dedupe(items):
    """Strip, drop blanks, and remove duplicates while preserving order."""
    seen, result = set(), []
    for item in items:
        value = str(item).strip()
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _valid_time(value):
    try:
        hh, mm = value.split(":")
        return 0 <= int(hh) <= 23 and 0 <= int(mm) <= 59
    except (ValueError, AttributeError):
        return False
