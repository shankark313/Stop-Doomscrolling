"""Shared helpers for reading and writing the AI Briefing config.

Persistence backends:
  - Supabase (production): used when SUPABASE_URL and SUPABASE_ANON_KEY are set.
    Railway's filesystem is ephemeral, so anything written to disk is lost on
    every redeploy/restart — the config lives in the `app_config` table instead.
    See supabase_schema.sql for the one-time table setup.
  - Local config.json (development fallback): used when the Supabase env vars
    are absent, and as a read-only fallback if Supabase is unreachable.
"""
import json
import os
from datetime import datetime, timezone

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")

SUPABASE_TABLE = "app_config"
SUPABASE_ROW_ID = "default"
_supabase_client = None


def _supabase():
    """Return a cached Supabase client, or None if env vars are not set."""
    global _supabase_client
    url = os.environ.get("SUPABASE_URL", "").strip()
    key = os.environ.get("SUPABASE_ANON_KEY", "").strip()
    if not (url and key):
        return None
    if _supabase_client is None:
        from supabase import create_client

        _supabase_client = create_client(url, key)
    return _supabase_client

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
    """Load the config, falling back to defaults for any missing keys.

    Reads from Supabase when configured, otherwise from local config.json.
    """
    client = _supabase()
    if client is not None:
        try:
            resp = (
                client.table(SUPABASE_TABLE)
                .select("config")
                .eq("id", SUPABASE_ROW_ID)
                .limit(1)
                .execute()
            )
            if resp.data:
                merged = dict(DEFAULT_CONFIG)
                merged.update(resp.data[0].get("config") or {})
                return merged
            # First run against an empty table: seed it with the defaults so
            # the row exists and later saves are plain upserts.
            _save_to_supabase(client, DEFAULT_CONFIG)
            return dict(DEFAULT_CONFIG)
        except Exception as exc:  # noqa: BLE001 - fall back rather than 500 on reads
            print(f"Supabase config read failed ({exc}); using local fallback.", flush=True)

    return _load_from_file()


def save_config(cfg):
    """Persist the config dict (Supabase when configured, else config.json)."""
    client = _supabase()
    if client is not None:
        # Let failures propagate: silently falling back to the ephemeral disk
        # here would reintroduce the "settings vanish on redeploy" bug.
        _save_to_supabase(client, cfg)
        return cfg
    return _save_to_file(cfg)


def _save_to_supabase(client, cfg):
    client.table(SUPABASE_TABLE).upsert(
        {
            "id": SUPABASE_ROW_ID,
            "config": cfg,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
    ).execute()
    return cfg


def _load_from_file():
    if not os.path.exists(CONFIG_PATH):
        _save_to_file(DEFAULT_CONFIG)
        return dict(DEFAULT_CONFIG)
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return dict(DEFAULT_CONFIG)

    merged = dict(DEFAULT_CONFIG)
    merged.update(data or {})
    return merged


def _save_to_file(cfg):
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
