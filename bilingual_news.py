from datetime import datetime, timezone, timedelta
import time
import feedparser
import requests
from deep_translator import GoogleTranslator

# Credentials (replace TELEGRAM_BOT_TOKEN with your active Bot Token)
TELEGRAM_BOT_TOKEN = "8900887284:AAFkOSAqyneCnJBcgXuswASwfIQs1qzAVk4"
TELEGRAM_CHAT_ID = "-1004348673663"

FEED_URL = "https://news.google.com/rss/search?q=(law+OR+court+OR+parliament+OR+judgment+OR+bill+OR+policy)+site:thestar.com.my+OR+site:bharian.com.my+OR+site:freemalaysiatoday.com+OR+site:malaysianbar.org.my+OR+site:jurist.org+OR+site:nst.com.my+OR+site:theedgemalaysia.com&hl=en-MY&gl=MY&ceid=MY:en"

def fetch_and_post_bilingual_news():
    translator = GoogleTranslator(source='en', target='ms')
    feed = feedparser.parse(FEED_URL)
    now = datetime.now(timezone.utc)
    
    for entry in feed.entries:
        # Check publication time: skip articles older than 40 minutes
        if hasattr(entry, 'published_parsed') and entry.published_parsed:
            published_dt = datetime.fromtimestamp(time.mktime(entry.published_parsed), tz=timezone.utc)
            if now - published_dt > timedelta(minutes=40):
                continue
                
        title_en = entry.title
        link = entry.link
        
        # Translate headline to Bahasa Malaysia
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

if __name__ == "__main__":
    fetch_and_post_bilingual_news()
