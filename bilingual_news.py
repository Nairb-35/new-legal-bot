import feedparser
import requests
import time
import os
from datetime import datetime, timezone, timedelta
from deep_translator import GoogleTranslator

# Matches your GitHub Secret names
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAMBOTTOKEN")
NOTION_TOKEN = os.getenv("NOTIONTOKEN")

TELEGRAM_CHAT_ID = "-1004348673663"
NOTION_DATABASE_ID = "3b0ffaadad14803f8aa7e4730248cb7"

FEED_URL = "https://news.google.com/rss/search?q=(law+OR+court+OR+parliament+OR+judgment+OR+bill+OR+policy)+site:thestar.com.my+OR+site:bharian.com.my+OR+site:freemalaysiatoday.com+OR+site:malaysianbar.org.my+OR+site:jurist.org+OR+site:nst.com.my+OR+site:theedgemalaysia.com&hl=en-MY&gl=MY&ceid=MY:en"

def push_to_notion(title_en, title_bm, link, date_str):
    if not NOTION_TOKEN:
        print("Error: NOTIONTOKEN environment variable is missing.")
        return None
        
    url = "https://api.notion.com/v1/pages"
    headers = {
        "Authorization": f"Bearer {NOTION_TOKEN}",
        "Content-Type": "application/json",
        "Notion-Version": "2022-06-28"
    }
    
    payload = {
        "parent": {"database_id": NOTION_DATABASE_ID},
        "properties": {
            "Name": {"title": [{"text": {"content": title_en}}]}
        },
        "children": [
            {"object": "block", "type": "heading_1", "heading_1": {"rich_text": [{"type": "text", "text": {"content": f"📰 {title_en}"}}]}},
            {"object": "block", "type": "paragraph", "paragraph": {"rich_text": [{"type": "text", "text": {"content": f"🇲🇾 {title_bm}"}}]}},
            {"object": "block", "type": "paragraph", "paragraph": {"rich_text": [{"type": "text", "text": {"content": f"📅 Date: {date_str}"}}]}},
            {"object": "block", "type": "paragraph", "paragraph": {"rich_text": [{"type": "text", "text": {"content": f"🔗 Article URL: {link}"}}]}},
            
            {"object": "block", "type": "heading_2", "heading_2": {"rich_text": [{"type": "text", "text": {"content": "🎬 Short-Form Educational Video Script (45–90s)"}}]}},
            {"object": "block", "type": "bulleted_list_item", "bulleted_list_item": {"rich_text": [{"type": "text", "text": {"content": f"🪝 Hook (0–5s): 'Did you know about this major legal update regarding {title_en}?'"}}]}},
            {"object": "block", "type": "bulleted_list_item", "bulleted_list_item": {"rich_text": [{"type": "text", "text": {"content": f"📰 News (5–25s): Breaking legal developments reported on {date_str}."}}]}},
            {"object": "block", "type": "bulleted_list_item", "bulleted_list_item": {"rich_text": [{"type": "text", "text": {"content": "⚖️ Why It Matters (25–50s): Statutory impact, fundamental rights, and legal significance."}}]}},
            {"object": "block", "type": "bulleted_list_item", "bulleted_list_item": {"rich_text": [{"type": "text", "text": {"content": "🧠 Key Takeaway (50–70s): Essential insight for law students and the public."}}]}},
            {"object": "block", "type": "bulleted_list_item", "bulleted_list_item": {"rich_text": [{"type": "text", "text": {"content": "🎤 Closing (70–90s): 'Follow for more Malaysian legal updates and interview prep!'"}}]}},

            {"object": "block", "type": "heading_2", "heading_2": {"rich_text": [{"type": "text", "text": {"content": "⚖️ LENS+ Law School Interview Analysis"}}]}},
            {"object": "block", "type": "bulleted_list_item", "bulleted_list_item": {"rich_text": [{"type": "text", "text": {"content": "L — Legal Issue: Main constitutional, criminal, or statutory issue."}}]}},
            {"object": "block", "type": "bulleted_list_item", "bulleted_list_item": {"rich_text": [{"type": "text", "text": {"content": "E — Explanation & Context: Facts summary and legal background."}}]}},
            {"object": "block", "type": "bulleted_list_item", "bulleted_list_item": {"rich_text": [{"type": "text", "text": {"content": "N — Necessary Legal Questions: Unresolved legal ambiguities & statutory gaps."}}]}},
            {"object": "block", "type": "bulleted_list_item", "bulleted_list_item": {"rich_text": [{"type": "text", "text": {"content": "S — Stakeholders & Significance: Impact on judiciary, public interest, and government."}}]}},
            {"object": "block", "type": "bulleted_list_item", "bulleted_list_item": {"rich_text": [{"type": "text", "text": {"content": "+ Personal Reasoned View: Balanced, mature legal opinion for an interview."}}]}},
            
            {"object": "block", "type": "heading_2", "heading_2": {"rich_text": [{"type": "text", "text": {"content": "🎯 Interview Answer & Follow-up Q&A"}}]}},
            {"object": "block", "type": "paragraph", "paragraph": {"rich_text": [{"type": "text", "text": {"content": f"🎤 60-Second Spoken Answer: 'A key legal issue in Malaysia is {title_en}. This raises important constitutional and statutory questions regarding...'"}}]}},
            {"object": "block", "type": "bulleted_list_item", "bulleted_list_item": {"rich_text": [{"type": "text", "text": {"content": "❓ 3 Follow-up Q&As: 1) Statutory basis? 2) Balancing competing rights? 3) Reform recommendations?"}}]}},
            {"object": "block", "type": "bulleted_list_item", "bulleted_list_item": {"rich_text": [{"type": "text", "text": {"content": "📚 5 Key Legal Terms: Statutory Interpretation, Judicial Review, Locus Standi, Ultra Vires, Ratio Decidendi."}}]}},
            {"object": "block", "type": "bulleted_list_item", "bulleted_list_item": {"rich_text": [{"type": "text", "text": {"content": "🎯 Interview Tips: Demonstrates legal awareness, critical thinking under Articles 5/8/10, and balanced reasoning."}}]}}
        ]
    }
    try:
        res = requests.post(url, json=payload, headers=headers)
        print("Notion API Status Code:", res.status_code)
        if res.status_code == 200:
            notion_page_url = res.json().get("url")
            print("Successfully created Notion page:", notion_page_url)
            return notion_page_url
        else:
            print("Notion Error Response:", res.text)
    except Exception as e:
        print("Notion Exception:", e)
    return None

def fetch_and_post_news(minutes_window=1440):
    translator = GoogleTranslator(source='en', target='ms')
    feed = feedparser.parse(FEED_URL)
    now = datetime.now(timezone.utc)
    posted_count = 0
    
    for entry in feed.entries:
        published_str = "Today"
        if hasattr(entry, 'published_parsed') and entry.published_parsed:
            published_dt = datetime.fromtimestamp(time.mktime(entry.published_parsed), tz=timezone.utc)
            if now - published_dt > timedelta(minutes=minutes_window):
                continue
            published_str = published_dt.strftime("%d %B %Y")
                
        title_en = entry.title
        link = entry.link
        
        try:
            title_bm = translator.translate(title_en)
        except Exception:
            title_bm = title_en
            
        # Push to Notion
        notion_url = push_to_notion(title_en, title_bm, link, published_str)
        if not notion_url:
            notion_url = link
            
        message = (
            f"🇬🇧 <b>{title_en}</b>\n"
            f"🇲🇾 <i>{title_bm}</i>\n"
            f"📅 <b>Tarikh / Date:</b> {published_str}"
        )
        
        reply_markup = {
            "inline_keyboard": [
                [
                    {"text": "🎬 Video Script Prompt", "url": notion_url},
                    {"text": "⚖️ LENS Analysis", "url": notion_url}
                ],
                [
                    {"text": "🔗 Baca Artikel / Read Article", "url": link}
                ]
            ]
        }
        
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message,
            "parse_mode": "HTML",
            "reply_markup": reply_markup
        }
        requests.post(url, json=payload)
        posted_count += 1
        
    return posted_count

if __name__ == "__main__":
    fetch_and_post_news(minutes_window=1440)
