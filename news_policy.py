"""Pure, conservative geography and duplicate rules for the news pipeline.

Publisher identity is never evidence of an article's geography. Unclear stories
remain unclassified instead of being sent to whichever feed happened to find them.
"""

import html
import re
import unicodedata
from collections import Counter
from difflib import SequenceMatcher
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


_PUBLISHERS = (
    "Free Malaysia Today", "Free Malaysia Today (FMT)", "FMT", "Malay Mail",
    "The Star", "TheStar", "The Star Online", "Bernama", "Berita RTM", "RTM",
    "New Straits Times", "NST Online", "NST", "The Edge Malaysia",
    "The Edge", "The Sun Daily", "TheSun", "The Sun", "Malaysiakini",
    "Sinar Harian", "Harian Metro", "Astro Awani", "Malaysia Gazette",
    "MalaysiaGazette", "Berita Harian",
    "Reuters", "AP", "Associated Press", "The Associated Press", "AP News",
    "BBC", "BBC News", "The Guardian", "CNN", "Al Jazeera", "The Straits Times",
    "Channel NewsAsia", "Channel News Asia", "CNA", "SCMP", "South China Morning Post",
    "Financial Times",
)
_PUBLISHER_DOMAINS = (
    "bernama.com", "freemalaysiatoday.com", "thestar.com.my", "malaymail.com",
    "rtm.gov.my", "reuters.com", "apnews.com", "bbc.com", "bbc.co.uk",
    "theguardian.com", "cnn.com", "aljazeera.com", "straitstimes.com",
    "channelnewsasia.com", "scmp.com", "ft.com",
)
_PUBLISHER_PATTERN = "(?:" + "|".join(re.escape(p) for p in sorted(_PUBLISHERS, key=len, reverse=True)) + "|(?:[a-z0-9-]+\\.)*(?:" + "|".join(re.escape(p) for p in _PUBLISHER_DOMAINS) + "))"
_PUBLISHER_SUFFIX = re.compile(r"\s+[\-|\u2013\u2014]\s*" + _PUBLISHER_PATTERN + r"\s*$", re.I)
_PUBLISHER_WORDS = re.compile(r"(?<!\w)" + _PUBLISHER_PATTERN + r"(?!\w)", re.I)
_DOMAIN_TEXT = re.compile(r"\b(?:https?://)?(?:[a-z0-9-]+\.)+(?:com|org|net|edu|gov|my|sg|uk|au|id|io|news|co)(?:/[^\s]*)?\b", re.I)


def _plain(value):
    return unicodedata.normalize("NFKC", html.unescape(re.sub(r"<[^>]*>", " ", str(value or ""))))


def _without_publisher(title):
    text = _plain(title).strip()
    while True:
        clean = _PUBLISHER_SUFFIX.sub("", text).strip()
        if clean == text:
            return clean
        text = clean


def normalize_headline(title):
    """Return a case/punctuation-normalized comparison key, removing known publishers only."""
    text = _without_publisher(title).casefold()
    # Treat formatting of thousands identically without erasing decimal values.
    text = re.sub(r"(?<=\d),(?=\d{3}(?:\D|$))", "", text)
    return " ".join(re.findall(r"[^\W_]+", text, flags=re.UNICODE))


def _terms(values):
    return re.compile(r"(?<!\w)(?:" + "|".join(re.escape(v) for v in sorted(values, key=len, reverse=True)) + r")(?!\w)", re.I)


_MALAYSIAN_PLACES = _terms((
    "Malaysia", "Malaysian", "Malaysians", "Malaysian-owned",
    "Johor", "Kedah", "Kelantan", "Melaka", "Malacca", "Negeri Sembilan",
    "Pahang", "Penang", "Pulau Pinang", "Perak", "Perlis", "Sabah", "Sarawak",
    "Selangor", "Terengganu", "Kuala Lumpur", "Putrajaya", "Labuan",
    "Petaling Jaya", "Shah Alam", "Johor Bahru", "Alor Setar", "Kota Bharu",
    "Kota Kinabalu", "Kuala Terengganu", "Kuching", "Miri", "Bintulu", "Sibu",
    "Sandakan", "Tawau", "Seremban", "Kuantan", "Ipoh", "Batu Pahat", "Klang",
    "Kajang", "Sepang", "Sungai Petani", "Kulim", "Taiping", "Temerloh",
    "Lahad Datu", "Cheras", "Ampang", "Bentong", "Port Dickson", "Langkawi",
    "Cyberjaya", "Kemaman", "Setapak", "Selayang", "Gombak", "Kota Tinggi",
    "Bukit Mertajam", "Kuala Selangor", "Kuala Kangsar", "Pasir Gudang",
))
_MALAYSIAN_INSTITUTIONS = _terms((
    "PDRM", "SPRM", "MACC", "Royal Malaysia Police", "Royal Malaysian Police",
    "Malaysian Anti-Corruption Commission", "Bank Negara Malaysia", "Bank Negara",
    "BNM", "KWSP", "PERKESO", "SOCSO", "KPKT", "JPJ", "LHDN", "KKM", "KPDN",
    "Jabatan Peguam Negara", "Mahkamah Persekutuan", "UMNO", "Bersatu",
    "Perikatan Nasional", "Pakatan Harapan", "Barisan Nasional",
    "Parti Amanah Negara", "Malaysian Chinese Association",
))
_MALAYSIAN_PEOPLE = _terms((
    "Anwar Ibrahim", "PM Anwar", "Najib Razak", "Muhyiddin Yassin",
    "Mahathir Mohamad", "Ismail Sabri", "Zahid Hamidi", "Ahmad Zahid",
    "Rafizi Ramli", "Nik Nazmi", "Tengku Zafrul", "Saifuddin Nasution",
    "Fahmi Fadzil", "Anthony Loke", "Nga Kor Ming", "Amir Hamzah Azizan",
    "Gobind Singh Deo", "Hannah Yeoh", "Dzulkefly Ahmad", "Azalina Othman",
    "Abdul Hadi Awang", "Syed Saddiq", "Lim Guan Eng", "Sultan Ibrahim",
))
# RTM sometimes translates Indonesia's DPR as Dewan Rakyat. An explicit foreign
# subject therefore overrides these two chamber labels unless Malaysia is explicit.
_MALAYSIAN_CHAMBERS = _terms(("Dewan Rakyat", "Dewan Negara"))
_FOREIGN = _terms((
    "United States", "United States of America", "Amerika Syarikat", "American",
    "Americans", "United Kingdom", "Britain", "British", "England", "English court",
    "Scotland", "Scottish", "Wales", "Welsh", "Northern Ireland", "Ireland", "Irish",
    "Singapore", "Singaporean", "Indonesia", "Indonesian", "Thailand", "Thai",
    "Philippines", "Filipino", "Filipinos", "Vietnam", "Vietnamese", "Cambodia",
    "Cambodian", "Laos", "Laotian", "Myanmar", "Burmese", "Brunei", "Timor-Leste",
    "China", "Chinese government", "Chinese court", "Beijing", "Hong Kong", "Taiwan",
    "Taiwanese", "Japan", "Japanese", "South Korea", "North Korea", "Korean",
    "India", "Indian government", "Indian court", "Pakistan", "Pakistani",
    "Bangladesh", "Bangladeshi", "Nepal", "Nepalese", "Sri Lanka", "Sri Lankan",
    "Afghanistan", "Afghan", "Maldives", "Bhutan", "Australia", "Australian",
    "New Zealand", "Canada", "Canadian", "Mexico", "Mexican", "Brazil", "Brazilian",
    "Argentina", "Argentine", "Chile", "Colombia", "Peru", "Venezuela", "Ecuador",
    "Cuba", "Haiti", "Jamaica", "France", "French", "Germany", "German", "Italy",
    "Italian", "Spain", "Spanish", "Portugal", "Portuguese", "Netherlands", "Dutch",
    "Belgium", "Belgian", "Switzerland", "Swiss", "Austria", "Sweden", "Swedish",
    "Norway", "Norwegian", "Denmark", "Danish", "Finland", "Finnish", "Iceland",
    "Poland", "Polish", "Czech", "Slovakia", "Hungary", "Romania", "Bulgaria",
    "Greece", "Greek", "Serbia", "Croatia", "Russia", "Russian", "Ukraine",
    "Ukrainian", "Belarus", "Georgia", "Turkey", "Turkiye", "Turkish", "Israel",
    "Israeli", "Palestine", "Palestinian", "Gaza", "West Bank", "Iran", "Iranian",
    "Iraq", "Iraqi", "Syria", "Syrian", "Lebanon", "Lebanese", "Jordan", "Saudi",
    "Saudi Arabia", "Yemen", "Yemeni", "Qatar", "Qatari", "Kuwait", "Bahrain",
    "Oman", "United Arab Emirates", "Egypt", "Egyptian", "Libya", "Libyan",
    "Tunisia", "Algeria", "Morocco", "Sudan", "South Sudan", "Ethiopia", "Kenya",
    "Uganda", "Tanzania", "Somalia", "Nigeria", "Nigerian", "Ghana", "South Africa",
    "Zimbabwe", "Zambia", "Mozambique", "Congo", "DR Congo", "Rwanda", "Mali",
    "Jakarta", "Bangkok", "London", "Washington", "Tokyo", "Moscow", "Kyiv", "Kiev",
    "Dhaka", "Kathmandu", "Manila", "Islamabad", "Karachi", "New Delhi", "Mumbai",
    "Chennai", "Seoul", "Paris", "Berlin", "Rome", "Madrid", "Brussels", "Geneva",
    "Stockholm", "Copenhagen", "Oslo", "Canberra", "Sydney", "Melbourne", "Ottawa",
    "Toronto", "Los Angeles", "New York", "San Francisco", "Riyadh", "Doha",
    "Abu Dhabi", "Dubai", "Tel Aviv", "Tehran", "Kabul", "Hanoi", "Phnom Penh",
    "Vientiane", "Naypyidaw", "Yangon", "Wellington", "Dublin", "Edinburgh",
    "Taipei", "Pyongyang", "Jerusalem", "Ramallah", "White House", "Downing Street",
    "Kremlin", "European Parliament", "European Union", "United Nations",
    "UN Security Council", "International Criminal Court", "International Court of Justice",
    "NATO", "World Health Organization", "Donald Trump", "Joe Biden", "Keir Starmer",
    "Vladimir Putin", "Volodymyr Zelensky", "Xi Jinping", "Narendra Modi",
    "Prabowo Subianto", "Benjamin Netanyahu", "Emmanuel Macron",
))
_FOREIGN_ABBREVIATIONS = re.compile(r"(?<!\w)(?:US|USA|U\.S\.|U\.S\.A\.|UK|U\.K\.|UAE|EU)(?!\w)")


def _geography_signals(text):
    text = _PUBLISHER_WORDS.sub(" ", _DOMAIN_TEXT.sub(" ", _without_publisher(text)))
    local = any(pattern.search(text) for pattern in (_MALAYSIAN_PLACES, _MALAYSIAN_INSTITUTIONS, _MALAYSIAN_PEOPLE))
    foreign = bool(_FOREIGN.search(text) or _FOREIGN_ABBREVIATIONS.search(text))
    if local:
        return "local"
    if foreign:
        return "international"
    if _MALAYSIAN_CHAMBERS.search(text):
        return "local"
    return None


def _malaysian_subject_in_summary(summary):
    """Find concrete Malaysian involvement, not a publisher or newsroom dateline."""
    text = _PUBLISHER_WORDS.sub(" ", _DOMAIN_TEXT.sub(" ", _without_publisher(summary)))
    if _MALAYSIAN_INSTITUTIONS.search(text) or _MALAYSIAN_PEOPLE.search(text):
        return True
    if re.search(r"\b(?:Malaysian(?:\s+|[-])(?:citizens?|nationals?|students?|tourists?|workers?|victims?|suspects?|companies|firms?|government|authorities|residents?)|warga\s+Malaysia|rakyat\s+Malaysia)\b", text, re.I):
        return True
    if re.search(r"\bMalaysia\s+(?:said|says|urges?|called|condemns?|announced|warns?|requested|seeks?|signed)\b", text, re.I):
        return True
    # A place introduced as the scene of events is stronger than a generic
    # nationality in the headline; a leading "KUALA LUMPUR:" dateline is not.
    return any(re.search(r"\b(?:in|at|near|outside|across|di)\s+$", text[max(0, match.start() - 15):match.start()], re.I)
               for match in _MALAYSIAN_PLACES.finditer(text))


def classify_geography(title, summary="", link=""):
    """Classify the subject, prioritizing the title and failing closed when unclear.

    A Malaysian subject with foreign connections is local. A foreign headline
    stays international despite an incidental Malaysian mention in its summary,
    but concrete Malaysian involvement (citizens or a local event) is local.
    URL section labels are only consulted when both texts are unclear.
    """
    title_result = _geography_signals(title)
    if title_result == "international" and _malaysian_subject_in_summary(summary):
        return "local"
    for text in (title, summary):
        result = _geography_signals(text)
        if result:
            return result
    try:
        path = urlsplit(str(link or "")).path.casefold()
    except ValueError:
        return None
    if re.search(r"/(?:world|international|global|dunia|luar-negara)(?:/|$)", path):
        return "international"
    if re.search(r"/(?:malaysia|malaysian-news|nasional|berita-tempatan)(?:/|$)", path):
        return "local"
    return None


_TRACKING_KEYS = frozenset((
    "fbclid", "gclid", "dclid", "msclkid", "mc_cid", "mc_eid", "igshid", "yclid",
    "_hsenc", "_hsmi", "vero_id", "mkt_tok", "spm", "cmpid", "ocid",
))
_UNRESERVED = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~")


def canonical_url(url):
    """Normalize a public article URL without dropping article IDs or search parameters."""
    value = html.unescape(str(url or "")).strip()
    try:
        parts = urlsplit(value)
        if parts.scheme.casefold() not in ("http", "https") or not parts.hostname or parts.username or parts.password:
            return ""
        host = parts.hostname.casefold()
        if any(c.isspace() for c in host):
            return ""
        if host.startswith("www."):
            host = host[4:]
        port = parts.port
        if ":" in host:
            host = f"[{host}]"
        if port is not None and not ((parts.scheme.casefold() == "https" and port == 443) or (parts.scheme.casefold() == "http" and port == 80)):
            host += f":{port}"
        path = re.sub(r"%[0-9a-fA-F]{2}", lambda m: chr(int(m[0][1:], 16)) if chr(int(m[0][1:], 16)) in _UNRESERVED else m[0].upper(), parts.path)
        path = path.rstrip("/") or "/"
        query = sorted((key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True)
                       if not key.casefold().startswith("utm_") and key.casefold() not in _TRACKING_KEYS)
        return urlunsplit(("https", host, path, urlencode(query), ""))
    except (TypeError, ValueError):
        return ""


_NUMBER_WORDS = {
    "zero": "0", "one": "1", "two": "2", "three": "3", "four": "4", "five": "5",
    "six": "6", "seven": "7", "eight": "8", "nine": "9", "ten": "10",
    "eleven": "11", "twelve": "12", "thirteen": "13", "fourteen": "14",
    "fifteen": "15", "sixteen": "16", "seventeen": "17", "eighteen": "18",
    "nineteen": "19", "twenty": "20", "satu": "1", "dua": "2", "tiga": "3",
    "empat": "4", "lima": "5", "enam": "6", "tujuh": "7", "lapan": "8",
    "sembilan": "9", "sepuluh": "10", "hundred": "hundred", "thousand": "thousand",
    "million": "million", "billion": "billion", "juta": "million", "bilion": "billion",
    "ribu": "thousand", "ratus": "hundred",
}
_FILLER = frozenset("a an the and of to in on at for from by with as is are was were be been says said say".split())
_ALIASES = {"govt": "government", "cops": "police"}


def _number_details(title):
    text = _without_publisher(title).casefold()
    numbers = [n.replace(",", "") for n in re.findall(r"\d+(?:[,.]\d+)*", text)]
    numbers.extend(_NUMBER_WORDS[word] for word in re.findall(r"[^\W_]+", text) if word in _NUMBER_WORDS)
    return Counter(numbers)


def _content_words(headline):
    return Counter(_ALIASES.get(word, word) for word in headline.split() if word not in _FILLER)


def is_duplicate(title, link, records):
    """Match URLs/headlines and only very conservative wording variants.

    Fuzzy matches must retain every meaningful word and all numeric details. This
    deliberately favors an occasional repeat over suppressing a distinct case or
    a changed charge, verdict, location, victim count, date, or amount.
    """
    url = canonical_url(link)
    headline = normalize_headline(title)
    numbers = _number_details(title)
    words = _content_words(headline)
    for record in records or ():
        if not isinstance(record, dict):
            continue
        if url and url == canonical_url(record.get("link", "")):
            return True
        other_title = record.get("title", "")
        other = normalize_headline(other_title)
        if not headline or not other:
            continue
        if headline == other:
            return True
        if len(headline.split()) < 7 or len(other.split()) < 7 or sum(words.values()) < 5:
            continue
        if numbers != _number_details(other_title) or words != _content_words(other):
            continue
        if SequenceMatcher(None, headline, other, autojunk=False).ratio() >= 0.9:
            return True
    return False
