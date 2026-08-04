import feedparser
import requests
import time
import os
import re
from datetime import datetime, timezone, timedelta
from deep_translator import GoogleTranslator

# Matches GitHub Secret names:
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAMBOTTOKEN")
NOTION_TOKEN = os.getenv("NOTIONTOKEN")

TELEGRAM_CHAT_ID = "-1004348673663"
NOTION_DATABASE_ID = "3b0ffaadad14803f8aa7e4730248cb7"
NOTION_DATABASE_URL = f"https://www.notion.so/{NOTION_DATABASE_ID}"

# Clean Google News RSS feed for Malaysian legal & political news.
FEED_URL = (
    "https://news.google.com/rss/search?q=(Malaysia+OR+Malaysian)+"
    "(law+OR+court+OR+parliament+OR+judgment+OR+bill+OR+police+OR+investigation+"
    "OR+charge+OR+policy+OR+politics+OR+minister+OR+cabinet+OR+election)+"
    "site:thestar.com.my+OR+site:freemalaysiatoday.com+OR+site:bharian.com.my+"
    "OR+site:nst.com.my+OR+site:theedgemalaysia.com+OR+site:sinarharian.com.my"
    "&hl=en-MY&gl=MY&ceid=MY:en"
)

NOTION_HEADERS = {
    "Authorization": f"Bearer {(NOTION_TOKEN or '').strip()}",
    "Content-Type": "application/json",
    "Notion-Version": "2022-06-28",
}

NON_LEGAL_TERMS = [
    r'\btennis\b', r'\bbasketball\b', r'\bbadminton\b', r'\bfood court\b',
    r'\bsports\b', r'\bmatch\b', r'\bchampion\b', r'\btournament\b', r'\bconcert\b',
    r'0\+ articles', r'articles\)'
]


# ---------------------------------------------------------------------------
# Classification & rating (unchanged logic)
# ---------------------------------------------------------------------------
def is_genuinely_legal_or_political(title, summary):
    text = f"{title} {summary}".lower()
    if any(re.search(term, text) for term in NON_LEGAL_TERMS):
        return False
    keywords = [
        r'court', r'law', r'parliament', r'judge', r'bill', r'policy',
        r'attorney general', r'constitution', r'legal', r'prosecutor', r'verdict',
        r'statute', r'judicial', r'amendment', r'bar council', r'tribunal', r'police',
        r'investigation', r'politics', r'political', r'minister', r'cabinet', r'election'
    ]
    return any(re.search(kw, text) for kw in keywords)


def get_importance_rating(title, summary):
    text = f"{title} {summary}".lower()
    if any(k in text for k in ['federal court', 'constitution', 'parliament passed', 'landmark', 'bill passed', 'cabinet decision']):
        return "⭐⭐⭐⭐⭐ (Landmark / Legislation)"
    elif any(k in text for k in ['court of appeal', 'high court', 'charged', 'macc', 'prosecutor', 'judicial review', 'minister', 'election']):
        return "⭐⭐⭐⭐☆ (Major Legal / Political Issue)"
    elif any(k in text for k in ['police', 'investigation', 'suspect', 'probe', 'abuse', 'policy', 'case']):
        return "⭐⭐⭐☆☆ (Important Case / Update)"
    else:
        return "⭐⭐☆☆☆ (Moderate Update)"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def normalize_title(title):
    """Drop the ' - Publisher' suffix Google News adds, lowercase, collapse spaces."""
    t = re.sub(r'\s*-\s*[^-]+$', '', title or '')
    return re.sub(r'\s+', ' ', t).strip().lower()


def tg(method, payload=None, params=None):
    """Call a Telegram Bot API method."""
    if not TELEGRAM_BOT_TOKEN:
        return None
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/{method}"
    try:
        # 60s so long-polling getUpdates(timeout=50) never times out the HTTP call.
        return requests.post(url, json=payload, params=params, timeout=60)
    except Exception as e:
        print("Telegram error:", method, e)
        return None


# ---------------------------------------------------------------------------
# Deduplication  ── THE FIX for repeated news.
# Google News RSS 'link' is an UNSTABLE redirect URL that changes between
# fetches, so the old "Source Link equals" check kept missing and re-posting.
# We now de-dupe primarily by the ARTICLE TITLE (stable), and by URL as a
# secondary guard.
# ---------------------------------------------------------------------------
def already_in_notion(title, link):
    if not NOTION_TOKEN:
        return False
    url = f"https://api.notion.com/v1/databases/{NOTION_DATABASE_ID}/query"
    payload = {
        "page_size": 1,
        "filter": {
            "or": [
                {"property": "Name", "title": {"equals": title[:200]}},
                {"property": "Source Link", "url": {"equals": link}},
            ]
        },
    }
    try:
        res = requests.post(url, json=payload, headers=NOTION_HEADERS, timeout=30)
        if res.status_code == 200:
            return len(res.json().get("results", [])) > 0
        print("Notion dedup query error:", res.status_code, res.text[:300])
    except Exception as e:
        print("Dedup error:", e)
    return False


# ---------------------------------------------------------------------------
# Notion page creation.
# NOTE: this now writes a "Date" property so the /search-by-date feature works.
# ⚠️ You must add a property called  Date  (type: Date)  to the Notion database
#    'Legal News & Interview Prep'. If it's missing, posting still works (it
#    retries without the date) but date-search will return nothing.
# ---------------------------------------------------------------------------
def push_to_notion(title_en, title_bm, link, date_str, date_iso, importance_stars):
    if not NOTION_TOKEN:
        print("Error: NOTIONTOKEN environment variable is missing.")
        return NOTION_DATABASE_URL

    url = "https://api.notion.com/v1/pages"
    children_blocks = [
        {"object": "block", "type": "heading_1", "heading_1": {"rich_text": [{"type": "text", "text": {"content": f"📰 {title_en[:200]}"}}]}},
        {"object": "block", "type": "paragraph", "paragraph": {"rich_text": [{"type": "text", "text": {"content": f"🇲🇾 {title_bm[:200]}"}}]}},
        {"object": "block", "type": "paragraph", "paragraph": {"rich_text": [{"type": "text", "text": {"content": f"📅 Date: {date_str} | Importance: {importance_stars}"}}]}},
        {"object": "block", "type": "paragraph", "paragraph": {"rich_text": [{"type": "text", "text": {"content": f"🔗 Article URL: {link}"}}]}},

        {"object": "block", "type": "heading_2", "heading_2": {"rich_text": [{"type": "text", "text": {"content": "🎬 Short-Form Educational Video Script (45–90s)"}}]}},
        {"object": "block", "type": "bulleted_list_item", "bulleted_list_item": {"rich_text": [{"type": "text", "text": {"content": f"🪝 Hook (0–5s): Did you know about this major update regarding {title_en[:100]}?"}}]}},
        {"object": "block", "type": "bulleted_list_item", "bulleted_list_item": {"rich_text": [{"type": "text", "text": {"content": f"📰 News (5–25s): Breaking legal/political developments reported on {date_str}."}}]}},
        {"object": "block", "type": "bulleted_list_item", "bulleted_list_item": {"rich_text": [{"type": "text", "text": {"content": "⚖️ Why It Matters (25–50s): Statutory impact, fundamental rights, and political significance."}}]}},
        {"object": "block", "type": "bulleted_list_item", "bulleted_list_item": {"rich_text": [{"type": "text", "text": {"content": "🧠 Key Takeaway (50–70s): Essential insight for law students and the public."}}]}},
        {"object": "block", "type": "bulleted_list_item", "bulleted_list_item": {"rich_text": [{"type": "text", "text": {"content": "🎤 Closing (70–90s): Follow for more Malaysian legal and political news analysis!"}}]}},

        {"object": "block", "type": "heading_2", "heading_2": {"rich_text": [{"type": "text", "text": {"content": "⚖️ LENS+ Law School Interview Analysis"}}]}},
        {"object": "block", "type": "bulleted_list_item", "bulleted_list_item": {"rich_text": [{"type": "text", "text": {"content": "L — Legal Issue: Main constitutional, criminal, political, or statutory issue."}}]}},
        {"object": "block", "type": "bulleted_list_item", "bulleted_list_item": {"rich_text": [{"type": "text", "text": {"content": "E — Explanation & Context: Facts summary and legal background."}}]}},
        {"object": "block", "type": "bulleted_list_item", "bulleted_list_item": {"rich_text": [{"type": "text", "text": {"content": "N — Necessary Legal Questions: Unresolved legal ambiguities & statutory gaps."}}]}},
        {"object": "block", "type": "bulleted_list_item", "bulleted_list_item": {"rich_text": [{"type": "text", "text": {"content": "S — Stakeholders & Significance: Impact on judiciary, public interest, and government."}}]}},
        {"object": "block", "type": "bulleted_list_item", "bulleted_list_item": {"rich_text": [{"type": "text", "text": {"content": "+ Personal Reasoned View: Balanced, mature legal opinion for an interview."}}]}},

        {"object": "block", "type": "heading_2", "heading_2": {"rich_text": [{"type": "text", "text": {"content": "🎯 Interview Answer & Follow-up Q&A"}}]}},
        {"object": "block", "type": "paragraph", "paragraph": {"rich_text": [{"type": "text", "text": {"content": f"🎤 60-Second Spoken Answer: 'A key issue in Malaysia is {title_en[:150]}. This raises important constitutional and statutory questions regarding...'"}}]}},
        {"object": "block", "type": "bulleted_list_item", "bulleted_list_item": {"rich_text": [{"type": "text", "text": {"content": "❓ 3 Follow-up Q&As: 1) Statutory basis? 2) Balancing competing rights? 3) Reform recommendations?"}}]}},
        {"object": "block", "type": "bulleted_list_item", "bulleted_list_item": {"rich_text": [{"type": "text", "text": {"content": "📚 5 Key Legal Terms: Statutory Interpretation, Judicial Review, Locus Standi, Ultra Vires, Ratio Decidendi."}}]}},
        {"object": "block", "type": "bulleted_list_item", "bulleted_list_item": {"rich_text": [{"type": "text", "text": {"content": "🎯 Interview Tips: Demonstrates legal awareness, critical thinking under Articles 5/8/10, and balanced reasoning."}}]}},
    ]

    properties = {
        "Name": {"title": [{"text": {"content": title_en[:200]}}]},
        "Source Link": {"url": link},
    }
    if date_iso:
        properties["Date"] = {"date": {"start": date_iso}}

    payload = {"parent": {"database_id": NOTION_DATABASE_ID}, "properties": properties, "children": children_blocks}
    try:
        res = requests.post(url, json=payload, headers=NOTION_HEADERS, timeout=30)
        if res.status_code == 200:
            return res.json().get("url")
        # If the DB has no "Date" column yet, retry without it so posting still works.
        if res.status_code == 400 and date_iso:
            properties.pop("Date", None)
            payload["properties"] = properties
            res = requests.post(url, json=payload, headers=NOTION_HEADERS, timeout=30)
            if res.status_code == 200:
                print("Posted without Date property — add a 'Date' (Date type) column to enable date search.")
                return res.json().get("url")
        print("Notion Error Response:", res.status_code, res.text[:400])
    except Exception as e:
        print("Notion Exception:", e)
    return NOTION_DATABASE_URL


def send_news_message(title_en, title_bm, published_str, importance_stars, notion_url, link):
    message = (
        f"🇬🇧 <b>{title_en}</b>\n"
        f"🇲🇾 <i>{title_bm}</i>\n"
        f"📅 <b>Tarikh / Date:</b> {published_str}\n"
        f"⭐ <b>Kepentingan / Importance:</b> {importance_stars}"
    )
    reply_markup = {
        "inline_keyboard": [
            [
                {"text": "🎬 Video Script Prompt", "url": notion_url},
                {"text": "⚖️ LENS Analysis", "url": notion_url},
            ],
            [{"text": "🔗 Baca Artikel / Read Article", "url": link}],
        ]
    }
    res = tg("sendMessage", {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
        "reply_markup": reply_markup,
        "disable_web_page_preview": False,
    })
    if res is not None:
        print("Telegram Send Status:", res.status_code)


# ---------------------------------------------------------------------------
# Main news pipeline
# ---------------------------------------------------------------------------
def fetch_and_post_news(minutes_window=1440):
    translator = GoogleTranslator(source='en', target='ms')
    feed = feedparser.parse(FEED_URL)
    now = datetime.now(timezone.utc)
    posted_count = 0
    seen_this_run = set()  # guards against duplicates WITHIN a single run

    print(f"Total entries fetched from Google News RSS: {len(feed.entries)}")

    for entry in feed.entries:
        title_en = (entry.title or "").strip()
        summary = getattr(entry, 'summary', '')
        link = entry.link

        if not is_genuinely_legal_or_political(title_en, summary):
            continue

        key = normalize_title(title_en)
        if not key or key in seen_this_run:
            continue

        # Time window + date
        date_iso = now.strftime("%Y-%m-%d")
        published_str = "Today"
        if getattr(entry, 'published_parsed', None):
            pub = datetime.fromtimestamp(time.mktime(entry.published_parsed), tz=timezone.utc)
            if now - pub > timedelta(minutes=minutes_window):
                continue
            published_str = pub.strftime("%d %B %Y")
            date_iso = pub.strftime("%Y-%m-%d")

        # Persistent de-dupe against Notion (fixes the repeats)
        if already_in_notion(title_en, link):
            seen_this_run.add(key)
            continue
        seen_this_run.add(key)

        importance_stars = get_importance_rating(title_en, summary)
        try:
            title_bm = translator.translate(title_en) or title_en
        except Exception:
            title_bm = title_en

        notion_url = push_to_notion(title_en, title_bm, link, published_str, date_iso, importance_stars) or NOTION_DATABASE_URL
        send_news_message(title_en, title_bm, published_str, importance_stars, notion_url, link)
        posted_count += 1
        time.sleep(1)  # be gentle with Telegram rate limits

    print(f"Posted {posted_count} new article(s).")
    return posted_count


# ---------------------------------------------------------------------------
# /search-by-date  ── the "search old news" button/command.
# Works on the stateless GitHub-Actions cron: each run it reads pending
# messages, answers any date searches, then acknowledges them so they are
# not handled twice. (So replies arrive within one cron cycle, up to ~30 min —
# not instant. For instant replies you'd need an always-on host / webhook.)
# ---------------------------------------------------------------------------
DATE_FORMATS = ["%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%d.%m.%Y",
                "%d %B %Y", "%d %b %Y", "%B %d %Y", "%B %d, %Y"]


def parse_date_query(text):
    t = re.sub(r'^/search(@\w+)?', '', (text or '').strip(), flags=re.I).strip()
    if not t:
        return None
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(t, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def query_notion_by_date(date_iso):
    if not NOTION_TOKEN:
        return []
    url = f"https://api.notion.com/v1/databases/{NOTION_DATABASE_ID}/query"
    payload = {"page_size": 25, "filter": {"property": "Date", "date": {"equals": date_iso}}}
    try:
        res = requests.post(url, json=payload, headers=NOTION_HEADERS, timeout=30)
        if res.status_code != 200:
            print("Notion date query error:", res.status_code, res.text[:300])
            return []
        out = []
        for pg in res.json().get("results", []):
            props = pg.get("properties", {})
            name = "".join(rt.get("plain_text", "") for rt in props.get("Name", {}).get("title", []))
            src = props.get("Source Link", {}).get("url")
            out.append({"title": name or "(untitled)", "link": src, "page": pg.get("url")})
        return out
    except Exception as e:
        print("Date query exception:", e)
        return []


def _send(chat_id, text, buttons=None):
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
    if buttons:
        payload["reply_markup"] = {"inline_keyboard": buttons}
    tg("sendMessage", payload)


def handle_update(u):
    """Handle ONE Telegram update (used by both the cron poller and the always-on bot)."""
    msg = u.get("message") or u.get("channel_post")
    if not msg:
        return
    text = (msg.get("text") or "").strip()
    chat_id = msg.get("chat", {}).get("id")
    if not text or chat_id is None:
        return

    low = text.lower()
    if low.startswith("/start") or low.startswith("/help"):
        _send(chat_id,
              "🔎 <b>Search past legal news</b>\n\n"
              "Send <code>/search YYYY-MM-DD</code> — e.g. <code>/search 2026-07-15</code> "
              "(also accepts <code>15/07/2026</code> or <code>15 July 2026</code>) — "
              "and I'll return every article saved for that date with its Video Script &amp; LENS links.")
        return

    if low.startswith("/search") or parse_date_query(text):
        date_iso = parse_date_query(text)
        if not date_iso:
            _send(chat_id, "Send a date like <code>/search 2026-07-15</code> or <code>/search 15 July 2026</code>.")
            return
        pretty = datetime.strptime(date_iso, "%Y-%m-%d").strftime("%d %B %Y")
        results = query_notion_by_date(date_iso)
        if not results:
            _send(chat_id, f"📭 No saved articles found for <b>{pretty}</b>.")
            return
        _send(chat_id, f"🗂 <b>{len(results)} article(s) on {pretty}:</b>")
        for r in results:
            buttons = []
            if r["page"]:
                buttons.append([{"text": "🎬 Video Script", "url": r["page"]},
                                {"text": "⚖️ LENS Analysis", "url": r["page"]}])
            if r["link"]:
                buttons.append([{"text": "🔗 Baca Artikel / Read Article", "url": r["link"]}])
            _send(chat_id, f"🇬🇧 <b>{r['title']}</b>", buttons or None)
            time.sleep(0.4)


def handle_commands():
    """Cron mode: drain pending updates, answer them, then acknowledge."""
    res = tg("getUpdates", params={"timeout": 0})
    if res is None or res.status_code != 200:
        return
    updates = res.json().get("result", [])
    if not updates:
        return
    last_id = None
    for u in updates:
        last_id = u["update_id"]
        try:
            handle_update(u)
        except Exception as e:
            print("handle_update error:", e)
    # Acknowledge processed updates so they aren't handled again next run.
    if last_id is not None:
        tg("getUpdates", params={"offset": last_id + 1, "timeout": 0})


def set_commands():
    tg("setMyCommands", {"commands": [
        {"command": "search", "description": "Search past legal news by date (e.g. /search 2026-07-15)"},
        {"command": "help", "description": "How to use this bot"},
    ]})


if __name__ == "__main__":
    set_commands()        # register the /search command menu (idempotent)
    handle_commands()     # answer any date-search requests waiting since last run
    fetch_and_post_news(minutes_window=1440)   # post fresh news (no repeats)
