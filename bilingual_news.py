import feedparser
import requests
import time
import os
import re
import json
from datetime import datetime, timezone, timedelta
from deep_translator import GoogleTranslator

# The bot borrows your existing LawGPT AI (Gemini via its proxy) to write a REAL,
# article-specific LENS analysis + video script — no separate API key needed.
# The proxy accepts requests from its own origins, so we send that Origin header.
AI_ENDPOINT = "https://lawgpt-app.vercel.app/api/claude"
AI_ORIGIN = "https://lawgpt-app.vercel.app"

# Matches GitHub Secret names:
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAMBOTTOKEN")
NOTION_TOKEN = os.getenv("NOTIONTOKEN")

TELEGRAM_CHAT_ID = "-1004348673663"
# Correct 32-char database id (the old one was missing a character, so every
# Notion save 404'd — which caused BOTH the repeated news and the broken
# "page couldn't be found" LENS/Video buttons).
NOTION_DATABASE_ID = "3b0ffaadad14803f8aa7e473024f8cb7"
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
def ai_lens(title, summary):
    """Ask the LawGPT AI for a SPECIFIC LIF (Legal Insight Framework) analysis +
    video script for this article. Returns a dict, or None on failure (then we
    fall back to a template)."""
    try:
        body = {
            "model": "claude-sonnet-4-6",
            "max_tokens": 4000,
            "system": (
                "You are a Malaysian law lecturer and interview coach. Turn ONE news item into a TIGHT, high-signal brief a "
                "law student can learn in 2–3 minutes. Work only from the headline and short snippet. Be SPECIFIC — never "
                "generic filler, never restate the headline, no padding. Ground everything in the real Malaysian legal "
                "framework and name the ACTUAL statutes or constitutional Articles that genuinely apply (e.g. Federal "
                "Constitution Art 5/8/10, Penal Code, Control of Supplies Act 1961, Criminal Procedure Code, Companies Act "
                "2016, PDPA 2010). Do NOT invent case citations, fake section numbers, or grand-sounding 'doctrines' that "
                "may not exist — if there is no established named doctrine, describe the principle in plain terms tied to the "
                "actual statute (e.g. 'government regulation of essential goods via statutory powers under the Control of "
                "Supplies Act 1961'), NOT an invented label. Be honest about certainty: separate what the article reports "
                "from your legal inference from a possible future development. Ratings are integers 1–5 and MUST use the full "
                "range — do not rate everything 4–5; reserve 5 for genuinely landmark items and use 1–2 for narrow/technical ones.\n"
                "Output ONLY valid JSON (no markdown fences) with EXACTLY these keys: "
                "jurisdiction (string, e.g. 'Malaysia'), "
                "areas (array of 2–4 short strings, e.g. 'Constitutional', 'Criminal'), "
                "lens (object with 'label' — ONE primary legal lens chosen from exactly: 'Constitutional Rights', "
                "'Criminal Liability', 'Administrative Power', 'Judicial Review', 'Contractual Obligations', "
                "'Corporate Governance', 'Statutory Regulation', 'Islamic/Syariah Law', 'Human Rights', 'Family Law' — "
                "and 'why' (1–2 sentences: the deeper recurring legal theme this article really teaches, beyond the surface story)), "
                "ratings (object with integer keys legal_impact, interview_value, exam_relevance, public_importance, longterm — each 1–5), "
                "brief (object with 'facts' (1–2 sentences: what actually happened, as reported), "
                "'statute' (the specific statute / constitutional Article / legal instrument that governs this — name it precisely, "
                "e.g. 'Control of Supplies Act 1961' or 'Art 11 Federal Constitution'; if genuinely none yet, say 'No specific statute — governed by common law / general principles'), "
                "and 'importance' (1–2 sentences: why it matters legally)), "
                "breakdown (object with 'principle' (the law behind this, grounded in a real statute/Article, NO invented doctrine names), "
                "'interests' (the competing values/interests in tension, one line), 'impact' (who is actually affected in practice, one line)), "
                "certainty (object with 'reported' (a fact the article states), 'implication' (your legal inference from it), "
                "'forecast' (a plausible future legal development) — each one sentence), "
                "interview (object with 'why_topic' (why an interviewer would pick this), 'insight' (one non-obvious observation), "
                "'followups' (array of exactly 3 likely follow-up questions), 'model_answer' (a strong ~30-second / ~70-word spoken answer)), "
                "think_deeper (string, ONE analytical open question), "
                "learn_more (object with 'act' (one relevant statute), 'case' (one relevant case, only if real — else a landmark principle), "
                "'issue' (one related legal issue to explore)), "
                "video (object for a 55–60s vertical short — keys: "
                "hooks (array of 2 scroll-stopping opening lines, each <= 10 words, one bold/provocative and one curiosity-gap), "
                "script (string, WORD-FOR-WORD spoken narration of ~130–150 words that reads naturally out loud in a punchy creator voice, "
                "opens with the strongest hook, explains the story and why it legally matters, and ends with a call to follow — no stage directions inside it), "
                "beats (array of 4–5 objects each with 'say' (the spoken line for this beat), 'caption' (short on-screen text overlay, <= 6 words) and 'visual' (a simple b-roll / shot suggestion)), "
                "broll (array of 10–16 SHORT, LITERAL stock-video search phrases — 2 to 4 plain English words each — ONE per sentence of the script, in the SAME order as the script; each must name a concrete, filmable thing that visually suits THAT sentence and that a free stock site would actually have, e.g. 'courtroom gavel', 'parliament building', 'police car lights', 'handcuffs close up', 'stock market screen', 'flooded street', 'hospital hallway', 'counting cash money', 'person signing contract', 'city skyline night'; use universal nouns only — NO proper names, NO Malaysian-specific terms, NO abstract ideas or statute numbers, translate the idea into a generic visual), "
                "takeaway (string, one memorable line usable as the pinned comment), "
                "post_caption (string, ready-to-paste caption for the post), "
                "hashtags (array of 5–7 short hashtag strings without spaces), "
                "title (string, a catchy <= 60 char video title))."
            ),
            "messages": [{"role": "user", "content": f"Headline: {title}\nSnippet: {summary or '(no snippet available)'}"}],
        }
        res = requests.post(AI_ENDPOINT, json=body,
                            headers={"Content-Type": "application/json", "Origin": AI_ORIGIN},
                            timeout=60)
        if res.status_code != 200:
            print("AI analysis error:", res.status_code, res.text[:200])
            return None
        text = "".join(b.get("text", "") for b in res.json().get("content", []) if b.get("type") == "text").strip()
        a, b = text.find("{"), text.rfind("}")
        if a < 0 or b <= a:
            return None
        return json.loads(text[a:b + 1])
    except Exception as ex:
        print("AI analysis exception:", ex)
        return None


# ---- tiny Notion block builders ----
def _rt(t):
    return {"rich_text": [{"type": "text", "text": {"content": str(t)[:1900]}}]}


def _h1(t): return {"object": "block", "type": "heading_1", "heading_1": _rt(t)}
def _h2(t): return {"object": "block", "type": "heading_2", "heading_2": _rt(t)}
def _p(t): return {"object": "block", "type": "paragraph", "paragraph": _rt(t)}
def _b(t): return {"object": "block", "type": "bulleted_list_item", "bulleted_list_item": _rt(t)}
def _q(t): return {"object": "block", "type": "quote", "quote": _rt(t)}
def _divider(): return {"object": "block", "type": "divider", "divider": {}}
def _code(t):
    return {"object": "block", "type": "code",
            "code": {"language": "plain text",
                     "rich_text": [{"type": "text", "text": {"content": str(t)[:1990]}}]}}


def _stars(n):
    """Turn an int 1–5 into a ⭐ / ☆ bar (defensive against bad AI output)."""
    try:
        n = max(0, min(5, int(n)))
    except Exception:
        n = 0
    return "⭐" * n + "☆" * (5 - n)


def _lens_badge(lens):
    """Return an emoji + label for the article's primary legal lens."""
    palette = {
        "Constitutional Rights": "🟦", "Criminal Liability": "🟩",
        "Administrative Power": "🟨", "Judicial Review": "🟥",
        "Contractual Obligations": "🟪", "Corporate Governance": "🟫",
        "Statutory Regulation": "🟧", "Islamic/Syariah Law": "🟩",
        "Human Rights": "🟦", "Family Law": "🟪",
    }
    label = (lens or {}).get("label", "") or "Legal Theme"
    return palette.get(label, "🧭"), label


def build_blocks(title_en, title_bm, link, date_str, importance_stars, a):
    """Lean, article-specific Notion blocks from the AI analysis `a` — a 2-3 minute
    read (Snapshot -> 60-Second Read -> Legal Lens -> Legal Breakdown -> Certainty
    -> Interview -> Think Deeper -> Learn More), with the video kit last."""
    v = a.get("video", {}) or {}
    r = a.get("ratings", {}) or {}
    brief = a.get("brief", {}) or {}
    bd = a.get("breakdown", {}) or {}
    cert = a.get("certainty", {}) or {}
    interview = a.get("interview", {}) or {}
    lm = a.get("learn_more", {}) or {}
    lens = a.get("lens", {}) or {}
    lens_emoji, lens_label = _lens_badge(lens)
    areas = ", ".join(a.get("areas") or []) or "—"

    blocks = [
        _h1(f"📰 {title_en[:190]}"),
        _p(f"🇲🇾 {title_bm[:190]}"),
        _p(f"📅 {date_str}   |   🌍 {a.get('jurisdiction', 'Malaysia')}   |   📚 {areas}"),
        _p(f"{lens_emoji} Legal Lens: {lens_label}"),
        _p(f"🔗 Article URL: {link}"),
        _divider(),

        _h2("⭐ Ratings"),
        _b(f"⚖️ Legal Impact:      {_stars(r.get('legal_impact'))}"),
        _b(f"🎓 Interview Value:   {_stars(r.get('interview_value'))}"),
        _b(f"📝 Exam Relevance:    {_stars(r.get('exam_relevance'))}"),
        _b(f"🌍 Public Importance: {_stars(r.get('public_importance'))}"),
        _b(f"🔮 Long-term Signif.: {_stars(r.get('longterm'))}"),
        _divider(),

        _h2("⚡ 60-Second Read"),
        _b(f"📋 Facts: {brief.get('facts', '')}"),
        _b(f"📜 Statute: {brief.get('statute', '')}"),
        _b(f"⭐ Importance: {brief.get('importance', '')}"),

        _h2(f"{lens_emoji} Legal Lens — {lens_label}"),
        _p(lens.get("why", "")),

        _h2("⚖️ Legal Breakdown"),
        _b(f"📖 Legal principle: {bd.get('principle', '')}"),
        _b(f"⚔️ Competing interests: {bd.get('interests', '')}"),
        _b(f"🌍 Practical impact: {bd.get('impact', '')}"),

        _h2("🧪 Fact vs Inference vs Forecast"),
        _b(f"✅ Reported: {cert.get('reported', '')}"),
        _b(f"⚖️ Legal implication: {cert.get('implication', '')}"),
        _b(f"💭 Could develop: {cert.get('forecast', '')}"),

        _h2("🎓 Interview Corner"),
        _b(f"Why interviewers like this: {interview.get('why_topic', '')}"),
        _b(f"One impressive insight: {interview.get('insight', '')}"),
        _b("Likely follow-up questions:"),
    ]
    for fu in (interview.get("followups") or [])[:3]:
        blocks.append(_b(f"    • {fu}"))
    blocks.append(_p(f"🎤 30-second model answer: {interview.get('model_answer', '')}"))

    blocks += [
        _h2("🧠 Think Deeper"),
        _q(a.get("think_deeper", "")),

        _h2("🔗 Learn More"),
        _b(f"📜 Act: {lm.get('act', '')}"),
        _b(f"📕 Case / principle: {lm.get('case', '')}"),
        _b(f"🧩 Related issue: {lm.get('issue', '')}"),
    ]
    return blocks


def build_video_blocks(title_en, title_bm, link, date_str, a):
    """The short-form VIDEO kit on its OWN Notion page — split out from the analysis
    so the two Telegram buttons open different pages."""
    v = a.get("video", {}) or {}
    blocks = [
        _h1(f"🎬 {title_en[:180]}"),
        _p(f"🇲🇾 {title_bm[:180]}"),
        _p(f"📅 {date_str}   |   🔗 {link}"),
        _divider(),
        _h2("🎬 Short-Form Video (record & post ready · ~55–60s)"),
    ]
    hooks = v.get("hooks")
    if not hooks and v.get("hook"):
        hooks = [v.get("hook")]
    if hooks:
        blocks.append(_b("🪝 Hook options (test both):"))
        for h in hooks[:2]:
            blocks.append(_b(f"    • {h}"))
    if v.get("title"):
        blocks.append(_b(f"🏷 Title: {v.get('title')}"))

    blocks.append(_h2("🎤 Word-for-Word Script"))
    blocks.append(_p(v.get("script", "")))

    beats = v.get("beats") or []
    if beats:
        blocks.append(_h2("🎬 Shot List (say / caption / visual)"))
        for i, bt in enumerate(beats[:5]):
            blocks.append(_b(f"{i + 1}. 🗣 {bt.get('say', '')}"))
            blocks.append(_b(f"    💬 {bt.get('caption', '')}   |   🎥 {bt.get('visual', '')}"))
    if v.get("takeaway"):
        blocks.append(_b(f"📌 Pinned-comment takeaway: {v.get('takeaway')}"))

    blocks.append(_h2("📱 Post Kit"))
    blocks.append(_p(f"Caption: {v.get('post_caption', '')}"))
    tags = " ".join(v.get("hashtags") or [])
    if tags:
        blocks.append(_p(f"Hashtags: {tags}"))

    # One-click render block: copy the whole code box, then run LawVideoMaker's
    # make.bat. Backgrounds are the AI's per-sentence b-roll terms (fallback: beat
    # visuals), so each video's footage suits its own topic.
    script_txt = (v.get("script") or "").strip()
    if script_txt:
        broll = v.get("broll") or [bt.get("visual", "") for bt in beats]
        broll = [re.sub(r"\s+", " ", str(x)).strip(" -•|") for x in broll]
        broll = [x for x in broll if x]
        payload = (
            "===LAWVID===\n"
            f"TITLE: {(v.get('title') or title_en)[:70]}\n"
            "SCRIPT:\n"
            f"{script_txt[:1500]}\n"
            "BROLL:\n"
            f"{' | '.join(broll)[:350]}\n"
            "===END==="
        )
        blocks.append(_divider())
        blocks.append(_h2("⚡ One-Click Render Block"))
        blocks.append(_p("Copy this whole box → double-click make.bat in LawVideoMaker → the finished video opens."))
        blocks.append(_code(payload))
    return blocks


def push_to_notion(title_en, title_bm, link, date_str, date_iso, importance_stars, analysis=None):
    # Returns the created page URL on success, or None on failure (so the caller
    # can skip Telegram and retry next run — never posting a duplicate or a
    # broken link).
    if not NOTION_TOKEN:
        print("Error: NOTIONTOKEN environment variable is missing.")
        return None

    url = "https://api.notion.com/v1/pages"
    if analysis:
        children_blocks = build_blocks(title_en, title_bm, link, date_str, importance_stars, analysis)
    else:
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

    return _create_page(title_en, link, date_iso, children_blocks)


def push_video_to_notion(title_en, title_bm, link, date_str, date_iso, analysis):
    """Create a SEPARATE Notion page holding only the video script, so the
    Telegram 'Video Script' button opens the video page (not the analysis)."""
    if not NOTION_TOKEN or not analysis:
        return None
    children_blocks = build_video_blocks(title_en, title_bm, link, date_str, analysis)
    return _create_page(f"🎬 {title_en[:190]}", link, date_iso, children_blocks)


def _create_page(name, link, date_iso, children_blocks):
    """Create one Notion page in the database; returns its URL or None.
    Retries without the Date property if the DB has no Date column."""
    url = "https://api.notion.com/v1/pages"
    properties = {
        "Name": {"title": [{"text": {"content": name[:200]}}]},
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
    return None


def _esc(t):
    """Escape for Telegram HTML parse mode."""
    return str(t or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def send_news_message(title_en, title_bm, published_str, importance_stars, notion_url, link, analysis=None, video_url=None):
    # Progressive disclosure: the concise 2-3 min read lives in the Telegram
    # message itself (snapshot + lens + 60-second read + ratings); the buttons
    # deep-link to the full Notion page for interview prep / video / deep analysis.
    a = analysis or {}
    brief = a.get("brief", {}) or {}
    lens = a.get("lens", {}) or {}
    r = a.get("ratings", {}) or {}
    lens_emoji, lens_label = _lens_badge(lens)

    lines = [
        f"🇬🇧 <b>{_esc(title_en)}</b>",
        f"🇲🇾 <i>{_esc(title_bm)}</i>",
    ]
    if lens_label:
        lines.append(f"{lens_emoji} <b>Legal Lens:</b> {_esc(lens_label)}")
    lines.append(f"📅 {_esc(published_str)}")

    if brief.get("facts") or brief.get("statute") or brief.get("importance"):
        lines.append("")
        lines.append("⚡ <b>60-Second Read</b>")
        if brief.get("facts"):
            lines.append(f"📋 <b>Facts:</b> {_esc(brief.get('facts'))}")
        if brief.get("statute"):
            lines.append(f"📜 <b>Statute:</b> {_esc(brief.get('statute'))}")
        if brief.get("importance"):
            lines.append(f"⭐ <b>Importance:</b> {_esc(brief.get('importance'))}")

    if r:
        lines.append("")
        lines.append(
            "⭐ <b>Ratings</b> — "
            f"Legal {_stars(r.get('legal_impact'))} · "
            f"Interview {_stars(r.get('interview_value'))} · "
            f"Exam {_stars(r.get('exam_relevance'))}"
        )
    else:
        lines.append(f"⭐ <b>Importance:</b> {_esc(importance_stars)}")

    message = "\n".join(lines)[:3900]
    reply_markup = {
        "inline_keyboard": [
            [
                {"text": "🎓 Interview & Deep Analysis", "url": notion_url},
                {"text": "🎬 Video Script", "url": video_url or notion_url},
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
def fetch_and_post_news(minutes_window=1440, max_posts=8):
    translator = GoogleTranslator(source='en', target='ms')
    feed = feedparser.parse(FEED_URL)
    now = datetime.now(timezone.utc)
    posted_count = 0
    seen_this_run = set()  # guards against duplicates WITHIN a single run

    print(f"Total entries fetched from Google News RSS: {len(feed.entries)}")

    for entry in feed.entries:
        try:
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

            importance_stars = get_importance_rating(title_en, summary)
            try:
                title_bm = translator.translate(title_en) or title_en
            except Exception:
                title_bm = title_en

            # Generate a REAL, article-specific LENS analysis + video script (falls
            # back to a template if the AI is unreachable).
            analysis = ai_lens(title_en, summary)

            # Save to Notion FIRST. Only if that succeeds do we send to Telegram and
            # mark it seen — so a Notion failure can never produce a duplicate post or
            # a broken button link; it simply retries on the next run.
            notion_url = push_to_notion(title_en, title_bm, link, published_str, date_iso, importance_stars, analysis)
            if not notion_url:
                print(f"Notion save failed — not posting (will retry next run): {title_en}")
                continue

            # Separate page for the video script so its Telegram button opens the
            # video (not the analysis). Best-effort: if it fails, the video button
            # falls back to the analysis page.
            video_url = push_video_to_notion(title_en, title_bm, link, published_str, date_iso, analysis)

            seen_this_run.add(key)
            send_news_message(title_en, title_bm, published_str, importance_stars, notion_url, link, analysis, video_url)
            posted_count += 1
            time.sleep(1)  # be gentle with Telegram rate limits
            if posted_count >= max_posts:
                break  # cap per run so a backlog trickles in over runs, not a flood

        except Exception as _e:
            print(f"Skipping one article due to error: {_e}")
            continue
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

    # Diagnostic: log the chat id of every update so we can find the correct
    # TELEGRAM_CHAT_ID after a group migration (supergroup ids change).
    chat_type = msg.get("chat", {}).get("type")
    print(f"Received update from chat_id={chat_id} (type={chat_type}) text={text[:40]!r}")

    low = text.lower()
    if low.startswith("/id"):
        _send(chat_id, f"🆔 This chat's ID is:\n<code>{chat_id}</code>\n\nSet this as the bot's TELEGRAM_CHAT_ID secret so news posts here.")
        return

    if low.startswith("/start") or low.startswith("/help"):
        _send(chat_id,
              "⚖️ <b>Malaysian Legal News Bot</b>\n\n"
              "📰 <code>/news</code> — check for the latest news right now\n"
              "🔎 <code>/search YYYY-MM-DD</code> — find past news by date "
              "(e.g. <code>/search 2026-07-15</code>, also accepts <code>15/07/2026</code> or <code>15 July 2026</code>)\n\n"
              "I also post fresh legal news automatically as it breaks.")
        return

    if low.startswith("/news"):
        # Check for fresh news on demand. Posts any new articles to the group
        # (deduped, so never a repeat); if nothing new, say so.
        _send(chat_id, "🔍 Checking for the latest legal news…")
        try:
            posted = fetch_and_post_news(minutes_window=1440)
        except Exception as e:
            print("/news fetch error:", e)
            posted = 0
        if not posted:
            _send(chat_id, "📭 <b>No news yet!</b>\nNothing new since the last update — I'll keep watching and post the moment something breaks. ⚖️")
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
        {"command": "news", "description": "Check for the latest legal news now"},
        {"command": "search", "description": "Search past legal news by date (e.g. /search 2026-07-15)"},
        {"command": "help", "description": "How to use this bot"},
    ]})


if __name__ == "__main__":
    set_commands()        # register the /search command menu (idempotent)
    handle_commands()     # answer any date-search requests waiting since last run
    fetch_and_post_news(minutes_window=1440)   # post fresh news (no repeats)
