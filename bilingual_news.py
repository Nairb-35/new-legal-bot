import feedparser
import requests
import time
import os
from datetime import datetime, timezone, timedelta
from deep_translator import GoogleTranslator

# Matches your exact GitHub Secret names:
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAMBOTTOKEN")
NOTION_TOKEN = os.getenv("NOTIONTOKEN")

TELEGRAM_CHAT_ID = "-1004348673663"
NOTION_DATABASE_ID = "3b0ffaadad14803f8aa7e4730248cb7"

FEED_URL = "https://news.google.com/rss/search?q=(law+OR+court+OR+parliament+OR+judgment+OR+bill+OR+policy)+site:thestar.com.my+OR+site:bharian.com.my+OR+site:freemalaysiatoday.com+OR+site:malaysianbar.org.my+OR+site:jurist.org+OR+site:nst.com.my+OR+site:theedgemalaysia.com&hl=en-MY&gl=MY&ceid=MY:en"

def get_next_run_info():
    now = datetime.now(timezone.utc)
    if now.minute < 30:
        next_run = now.replace(minute=30, second=0, microsecond=0)
    else:
        next_run = (now + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
    
    diff_seconds = int((next_run - now).total_seconds())
    mins, secs = divmod(diff_seconds, 60)
    myt_time = (next_run + timedelta(hours=8)).strftime("%I:%M %p MYT")
    return mins, secs, myt_time

def push_to_notion(title_en, title_bm, link):
    if not NOTION_TOKEN:
        return
    url = "https://api.notion.com/v1/pages"
    headers = {
        "Authorization": f"Bearer {NOTION_TOKEN}",
        "Content-Type": "application/json",
        "Notion-Version": "2022-06-28"
    }
    payload = {
        "parent": {"database_id": NOTION_DATABASE_ID},
        "properties": {
            "Name": {"title": [{"text": {"content": title_en}}]},
            "Source Link": {"url": link}
        },
        "children": [
            {"object": "block", "type": "heading_2", "heading_2": {"rich_text": [{"type": "text", "text": {"content": "🇲🇾 Bahasa Malaysia Headline"}}]}},
            {"object": "block", "type": "paragraph", "paragraph": {"rich_text": [{"type": "text", "text": {"content": title_bm}}]}},
            {"object": "block", "type": "heading_2", "heading_2": {"rich_text": [{"type": "text", "text": {"content": "🎓 LENS+ Law Interview Prep Framework"}}]}},
            {"object": "block", "type": "bulleted_list_item", "bulleted_list_item": {"rich_text": [{"type": "text", "text": {"content": "L — Legal Issue: Main constitutional/statutory issue."}}]}},
            {"object": "block", "type": "bulleted_list_item", "bulleted_list_item": {"rich_text": [{"type": "text", "text": {"content": "E — Explanation: Context and background details."}}]}},
            {"object": "block", "type": "bulleted_list_item", "bulleted_list_item": {"rich_text": [{"type": "text", "text": {"content": "N — Necessary Legal Questions: Key legal challenges & statutory gaps."}}]}},
            {"object": "block", "type": "bulleted_list_item", "bulleted_list_item": {"rich_text": [{"type": "text", "text": {"content": "S — Stakeholders: Impact on citizens, judiciary, and government."}}]}}
        ]
    }
    requests.post(url, json=payload, headers=headers)

def fetch_and_post_news(minutes_window=40):
    translator = GoogleTranslator(source='en', target='ms')
    feed = feedparser.parse(FEED_URL)
    now = datetime.now(timezone.utc)
    posted_count = 0
    
    for entry in feed.entries:
        if hasattr(entry, 'published_parsed') and entry.published_parsed:
            published_dt = datetime.fromtimestamp(time.mktime(entry.published_parsed), tz=timezone.utc)
            if now - published_dt > timedelta(minutes=minutes_window):
                continue
                
        title_en = entry.title
        link = entry.link
        
        try:
            title_bm = translator.translate(title_en)
        except Exception:
            title_bm = title_en
            
        message = (
            f"🇬🇧 <b>{title_en}</b>\n"
            f"🇲🇾 <i>{title_bm}</i>\n\n"
            f"🔗 <a href='{link}'>Baca Artikel / Read Article</a>"
        )
        
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        requests.post(url, data={"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"})
        push_to_notion(title_en, title_bm, link)
        posted_count += 1
        
    return posted_count

if __name__ == "__main__":
    fetch_and_post_news(minutes_window=10080)
