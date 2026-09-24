"""Load shared news history for conservative cross-topic duplicate checks."""
from datetime import datetime, timedelta, timezone


def load_recent_news_history(post, database_id, headers, days=14):
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    payload = {
        "page_size": 100,
        "filter": {"timestamp": "created_time", "created_time": {"on_or_after": since}},
        "sorts": [{"timestamp": "created_time", "direction": "descending"}],
    }
    records = []
    cursors = set()
    for _ in range(50):
        response = post(f"https://api.notion.com/v1/databases/{database_id}/query",
                        json=payload, headers=headers, timeout=30)
        if response.status_code != 200:
            raise RuntimeError(f"News history unavailable (HTTP {response.status_code}); posting paused")
        data = response.json()
        if not isinstance(data.get("results"), list):
            raise RuntimeError("News history response invalid; posting paused")
        for page in data["results"]:
            props = page.get("properties", {})
            title = "".join(part.get("plain_text", part.get("text", {}).get("content", ""))
                            for part in props.get("Name", {}).get("title", []))
            link = props.get("Source Link", {}).get("url") or ""
            if title and not title.startswith("🎬"):
                records.append({"title": title, "link": link})
        if not data.get("has_more"):
            return records
        cursor = data.get("next_cursor")
        if not cursor or cursor in cursors:
            raise RuntimeError("News history pagination incomplete; posting paused")
        cursors.add(cursor)
        payload = {**payload, "start_cursor": cursor}
    raise RuntimeError("News history scan limit reached; posting paused")
