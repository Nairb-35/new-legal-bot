import feedparser
import requests
from deep_translator import GoogleTranslator

# Credentials
TELEGRAM_BOT_TOKEN = "8900887284:AAEQQDF3dDxnofgy76u6Km3efzMvOVZAT4I"
TELEGRAM_CHAT_ID = "-1004348673663"

# RSS Feed Source
FEED_URL = "https://news.google.com/rss/search?q=(law+OR+court+OR+parliament+OR+judgment+OR+bill+OR+policy)+site:thestar.com.my+OR+site:bharian.com.my+OR+site:freemalaysiatoday.com+OR+site:malaysianbar.org.my+OR+site:jurist.org+OR+site:nst.com.my+OR+site:theedgemalaysia.com&hl=en-MY&gl=MY&ceid=MY:en"

def fetch_and_post_bilingual_news():
    translator = GoogleTranslator(source='en', target='ms')
    feed = feedparser.parse(FEED_URL)
    
    for entry in feed.entries[:3]:
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
