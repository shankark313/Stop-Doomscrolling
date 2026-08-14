# 🔧 Troubleshooting

Diagnosing why a briefing arrived thin, and the maintenance this app needs to
keep working.

---

## Start here: the SOURCE HEALTH line

Every run prints one line summarising what each source actually returned. On
Railway, open **Deploy Logs** and filter for `SOURCE HEALTH`:

```
SOURCE HEALTH — youtube: 8/9 channels, 10 video(s) | rss: 39 post(s) |
reddit: 13/13 subs, 61 post(s) | producthunt: 3188 chars | topics: 25/25 with results
```

Read it before assuming the briefing is wrong. A missing section in Telegram has
two very different causes, and this line tells them apart:

- **The source returned nothing** → a fetch problem. Fix it below.
- **The source returned plenty but the section is still missing** → a curation
  problem. See [Topics missing from the briefing](#topics-missing-from-the-briefing).

A lane reading `0` almost never means "there was no news today". It means that
source failed.

---

## `youtube: 0/9 channels`

**Cause.** YouTube serves a "Sign in to confirm you're not a bot" interstitial to
datacenter IPs. The same channels work fine from a laptop on a residential
connection, which makes this confusing to diagnose — it only fails in production.

Confirm it by filtering the logs for `bot`:

```
ERROR: [youtube] aZN8Jy0akYg: Sign in to confirm you're not a bot.
```

**Fix.** Give yt-dlp a logged-in cookie jar via `YTDLP_COOKIES_B64`.

### Exporting YouTube cookies

Cookies are **invalidated when the browser session that created them stays
active**, so exporting from your everyday Chrome profile produces cookies that
break within a day or two. Use a private window you then abandon:

1. Install **"Get cookies.txt LOCALLY"** in Chrome (open-source; exports without
   sending anything to a server — avoid the older "cookies.txt" extensions).
2. `chrome://extensions` → the extension → enable **Allow in Incognito**.
3. Open an **Incognito window**, go to `youtube.com`, and **log in**. Prefer a
   throwaway/secondary Google account: these cookies grant access to whatever
   account you use.
4. Extension → **Export** → save `cookies.txt`.
5. **Close the Incognito window without signing out.** Signing out invalidates
   the export.

### Installing them

```bash
base64 -i ~/Downloads/www.youtube.com_cookies.txt | tr -d '\n' | pbcopy
```

Paste into Railway as `YTDLP_COOKIES_B64` and **SHARE** it to the service.

Base64 is used rather than the raw file because `cookies.txt` is **tab**-separated
and dashboard textareas silently convert tabs to spaces, which parses as an empty
cookie jar and fails identically to having no file at all. Whitespace inside the
base64 value is harmless — the decoder strips it — so a line-wrapped paste still
works.

Verify from the logs:

```
Using YouTube cookie file with 23 cookie(s).
```

If the count is wrong the paste was truncated. If the line is absent entirely,
the variable isn't linked to the service.

> **These cookies expire — expect to redo this every few weeks.** When they do,
> `SOURCE HEALTH` returns to `youtube: 0/9`. Nothing else breaks.

**Alternative.** `YTDLP_PROXY` pointed at a residential proxy avoids the cookie
rotation entirely, at the cost of a proxy subscription.

---

## `reddit: 0/13 subs` (or 1–2 of them)

**Cause.** Reddit is effectively unreachable from a server:

- The JSON API (`/top.json`) answers **403** to non-OAuth clients — including
  from a home connection.
- The RSS fallback answers **429 after roughly two subreddits**. This is a
  cumulative per-IP limit, so increasing the delay between subreddits does *not*
  help. With 13 subreddits configured you get one or two through at best.

**Fix.** Set `SERPER_API_KEY`. The app reads Reddit through Google's index
(`site:reddit.com/r/<sub>` with a 24-hour filter), which has no per-subreddit
rate limit. Reddit's own endpoints stay as the fallback.

The tradeoff: Google results carry no post score, so the `score` field is `None`
on Serper-sourced posts. Curation is unaffected.

---

## Topics missing from the briefing

If `SOURCE HEALTH` shows `topics: 25/25 with results` but your narrower topics
never appear, the material was fetched and then dropped during curation.

**Historical cause.** The duration setting hard-coded a story count ("8–10
stories" for `1hr`) with no knowledge of how many topics were configured. Against
a 25-topic list, the large model and funding stories consumed the entire budget
while the operational topics the reader specifically added went unmentioned.

**Current behaviour.** Duration now controls *depth per story*; `max_tokens`
scales with topic count, and the prompt requires at least one item per topic that
has material. Expect a 25-topic briefing to run ~10 Telegram messages.

If a *specific* topic is consistently empty, check whether it's actually a topic.
Style instructions ("in plain English, no jargon") are not searchable and will
log `Serper returned no results` every run — those belong in the system prompt,
not the topic list. Also watch for near-duplicates (`Latest AI news` vs
`latest AI News`), which cost double the API calls for identical results.

---

## Product Hunt shows the Exa fallback every run

```
Product Hunt leaderboard empty/blocked; falling back to Exa (producthunt.com).
```

This is **normal, not an error.** Today's leaderboard reads "No posts for this
date" until end of day PST, and Product Hunt renders its ranked list with
JavaScript that `r.jina.ai` cannot execute. The Exa fallback scoped to
`producthunt.com` is the working path; the prompt enforces today-only filtering.

---

## Nothing arrives in Telegram at all

- `Telegram token or chat id missing` → `TELEGRAM_BOT_TOKEN` unset, or no chat ID
  in either the UI or `TELEGRAM_CHAT_ID`.
- `Telegram HTML send failed (400); retrying as plain text` → a malformed tag in
  Claude's output. Delivery still succeeds, just unformatted. Not fatal.
- Settings reverting after a redeploy → `SUPABASE_URL` / `SUPABASE_ANON_KEY` are
  unset and the app is writing to Railway's ephemeral disk.

---

## Railway shared variables

A variable added under Project Settings → Shared Variables does nothing until you
click **SHARE** and link it to the service. An unlinked variable produces exactly
the same symptom as a missing one, so check the link before re-issuing keys.

The startup log lists which credentials the process can actually see:

```
=== AI Briefing startup: environment check ===
  ANTHROPIC_API_KEY set: True
  ...
```
