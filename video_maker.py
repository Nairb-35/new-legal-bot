"""
video_maker.py - renders a vertical law short from (title, script, broll_terms).
Runs on the GitHub Actions runner (called by bilingual_news.py). Uses the bundled
`lib/` background clips (keyword-matched) so it never depends on a live stock site.
Deps (edge-tts, imageio-ffmpeg, pillow, numpy) are installed on demand by the caller.
"""
import asyncio, os, subprocess, re, glob, secrets
import numpy as np
import edge_tts, imageio_ffmpeg
from PIL import Image, ImageDraw, ImageFilter

HERE = os.path.dirname(os.path.abspath(__file__))
LIB = os.path.join(HERE, "lib")
WORK = os.path.join(HERE, "_vwork")
os.makedirs(WORK, exist_ok=True)
FF = imageio_ffmpeg.get_ffmpeg_exe()
VOICE, RATE = "en-US-AndrewNeural", "+15%"
HFPS = 15
PEXELS_PROXY = "https://new-legal-bot.vercel.app/api/pexels"   # returns a fresh Pexels clip URL if a key is set

def _pexels_fetch(term, idx):
    """Ask the Vercel proxy for a fresh Pexels clip for this term and download it
    (unique footage per video). Returns a local path, or None to fall back to lib."""
    try:
        import requests
        r = requests.get(PEXELS_PROXY, params={"q": term, "i": idx}, timeout=15)
        u = (r.json() or {}).get("url")
        if not u:
            return None
        slug = re.sub(r"[^a-z0-9]+", "_", (term or "").lower()).strip("_")[:36]
        dst = os.path.join(WORK, f"px_{slug}_{idx}.mp4")
        if os.path.exists(dst) and os.path.getsize(dst) > 200000:
            return dst
        with requests.get(u, timeout=90, stream=True) as g:
            with open(dst, "wb") as f:
                for chunk in g.iter_content(65536):
                    f.write(chunk)
        return dst if (os.path.exists(dst) and os.path.getsize(dst) > 200000) else None
    except Exception:
        return None

# ---- map an AI b-roll term to one of the bundled library clips ----
LIB_MAP = [
    (("sign","signature","contract","pen"), "signing"),
    (("prison","jail","cell","convict","detain","inmate","sentence"), "prison"),
    (("police","arrest","officer","raid","cop","crime scene"), "police"),
    (("money","cash","fund","fine","fraud","bribe","financial","salary","tax","payment","bank"), "money"),
    (("court","judge","gavel","ruling","ruled","tribunal","verdict","judicial","trial","lawsuit"), "court"),
    (("parliament","minister","government","cabinet","ministry","official","policy","authority","department"), "building"),
    (("student","study","university","school","exam","class","learn","campus"), "students"),
    (("road","traffic","car","highway","vehicle","drive","accident"), "road"),
    (("document","file","paper","report","record","form","desk"), "document"),
    (("office","company","corporate","business","work","employee","meeting","deal","agreement","handshake","negotiat"), "office"),
    (("stair","climb","journey","rise","steps","struggle","fight all"), "stairs"),
    (("protest","rally","crowd","demonstration","march","riot"), "protest"),
    (("family","child","kid","home","parent","together","baby","custody"), "family"),
    (("mother","woman","man","person","worried","sad","think","alone","victim","people","citizen"), "person"),
    (("city","skyline","kuala","urban","town","night","street"), "city"),
    (("book","law","read","word","letter","constitution","statute","act","clause","interpret","rule","page","fine print"), "book"),
]
DEFAULT_ROT = ["court", "building", "book", "city", "office", "document"]

# ---- CARTOON (kampung doodle) mode ----
# Static cartoon backgrounds in lib/toon/ (user-drawn in Gemini, matching the host).
# When toon mode is on, we map each b-roll term to one of these images instead of a
# stock video clip, so the whole video is in the hand-drawn style.
TOON_DIR = os.path.join(LIB, "toon")
TOON_HQ_DIR = os.path.join(LIB, "toon_hq")
TOON_ACTION_DIR = os.path.join(LIB, "toon_action")
# Dedicated reenactments take priority over symbolic legal plates. These are
# intentionally narrow matches: a generic story about a weapon should not show
# the stabbing scene, while a described case should show people doing the act.
TOON_ACTION_MAP = [
    (("deliberately stab", "intending to kill", "intent to kill", "intentional killing",
      "intentional attack", "knife attack"), "intentional_attack"),
    (("one punch", "single punch", "punch causation", "punch legally caused"), "one_punch"),
    (("fragile skull", "thin-skull", "thin skull"), "fragile_skull"),
    (("danger ends", "danger has ended", "keeps attacking", "continued attack",
      "excessive retaliation", "excessive self-defence", "disproportionate self-defence"),
     "excessive_defence"),
    (("packed station", "crowded station", "crowded train", "imminently dangerous",
      "throws a bomb", "bomb into"), "crowded_station_danger"),
]
TOON_MAP = [
    # order matters: most specific first, generic/process words last
    (("handcuff","arrest","raid","detain","caught","apprehend","nab"), "arrest"),
    (("police","officer","cop","patrol","enforcement","pdrm"), "police"),
    (("station","lodge","report a","police report","counter","complaint"), "station"),
    (("investigat","evidence","forensic","fingerprint","detective","probe","clue","dna","sample"), "investigation"),
    (("interrogat","question","statement","interview","confession","remand"), "interrogation"),
    (("document","file","paper","record","form","folder","affidavit","paperwork"), "documents"),
    (("sign","signature","stamp","seal","endorse","execute the"), "signature"),
    (("book","law","statute","act ","section","penal","clause","constitution","code","legislation"), "book"),
    (("charge","accused","indict","prosecut the","framed","allegation"), "charge"),
    (("lawyer","advocate","solicitor","counsel","consult","legal advice","represent"), "lawyer"),
    (("court","trial","hearing","tribunal","lawsuit","courtroom","proceeding"), "court"),
    (("judge","magistrate","bench","preside","his lordship"), "judge"),
    (("witness","testif","testimony","stand","cross-examin","give evidence"), "witness"),
    (("verdict","guilty","convict","acquit","ruling","ruled","found guilty","judgment"), "verdict"),
    (("justice","scales","fair","fairness","equality","balance","impartial"), "justice"),
    (("sentenc","punish","penalty","jail term","imprison","fine ","whipping"), "sentence"),
    (("jail","prison","cell","bars","lockup","custody","behind bars","incarcerat"), "jail"),
    (("bail","release","freed","bond","surety"), "bail"),
    (("appeal","higher court","federal court","overturn","review the"), "appeal"),
    (("money","cash","bribe","corrupt","payment","fund","ringgit","rm ","financial"), "money"),
    (("theft","steal","stolen","robber","burglar","snatch","shoplif"), "theft"),
    (("weapon","knife","gun","firearm","parang","assault","attack","violen"), "weapon"),
    (("drug","dadah","narcotic","trafficking","possession of","substance"), "drugs"),
    (("scam","fraud","cheat","phishing","online","cyber","deceive","phone call"), "scam"),
    (("accident","crash","collision","road accident","injur","victim","hurt"), "accident"),
    (("road","car","vehicle","traffic","driving","drive","highway","summons"), "road"),
    (("family","home","house","parent","child","spouse","domestic","household"), "family"),
    (("protest","crowd","rally","demonstrat","assembly","gathering","public order"), "protest"),
    (("city","street","urban","building","town","neighbourhood","skyline"), "city"),
    (("deal","handshake","agree","contract","settlement","negotiat","sign the deal"), "deal"),
    (("meeting","discuss","office","desk","work","staff","colleague","conference"), "office"),
    (("right","freedom","liberty","entitle","protect you","know your"), "rights"),
    (("clock","time","deadline","hour","period","limitation","expire","24"), "clock"),
    (("malaysia","flag","nation","country","government","federal"), "malaysia"),
    (("hospital","doctor","medical","clinic","treatment","health","ambulance"), "hospital"),
    (("student","school","teacher","learn","class","education","young"), "students"),
    # --- second wave: MY-specific offences & civil topics ---
    (("corrupt","corruption","macc","sprm","graft","kickback","embezzl","abuse of power","bribe"), "corruption"),
    (("immigration","passport","visa","migrant","deport","border"), "immigration"),
    (("roadblock","checkpoint","sekatan","breathalys","spot check"), "roadblock"),
    (("summons","saman","ticket","compound","notice to"), "summons"),
    (("tenant","landlord"," rent","tenancy","lease","eviction"), "tenancy"),
    (("marriage","married","marry","wedding","divorce","matrimon","nikah"), "marriage"),
    (("inherit","will ","estate","testament","beneficiar","probate","faraid"), "inheritance"),
    (("employ","worker","salary","wage","dismiss","termination","labour"), "employment"),
    (("syariah","shariah","islamic law","fatwa","khalwat"), "syariah"),
    (("customs","smuggl","contraband","import","export duty"), "customs"),
    (("gambl","betting","casino"," 4d","lottery","judi"), "gambling"),
    (("drink driv","drunk","alcohol","dui","intoxicat"), "drinkdriving"),
    (("vandal","graffiti","damage property","mischief"), "vandalism"),
    (("cyber","computer","hack","data breach","malware","digital","phishing site"), "cyber"),
    (("defam","slander","libel","social media","viral post","reputation"), "defamation"),
    (("fire","arson","burn","flame","blaze"), "fire"),
    (("flood","disaster","landslide","earthquake","haze"), "disaster"),
    (("bank","atm","account","deposit","withdraw","cheque"), "bank"),
    (("insurance","claim","policy","premium","compensat"), "insurance"),
    (("airport","flight","travel","plane","board a"), "airport"),
    (("cctv","camera","surveillance","footage","recorded"), "cctv"),
    (("oath","swear","affirm","under oath","pledge"), "oath"),
    (("mediat","settle out","reconcil","negotiate a","compromise"), "mediation"),
    (("seiz","confiscat","bailiff","repossess","impound"), "seizure"),
    (("debt","loan","borrow","creditor","owed"," owe ","repay"), "debt"),
    (("custody","guardian","adopt","welfare of the child"), "custody"),
    (("harass","stalk","threaten","intimidat","bully"), "harassment"),
    (("tax","lhdn","income tax","levy","duty"), "tax"),
    (("company","business","ssm","director","corporate","firm"), "company"),
    (("election","vote","ballot","polling","candidate","campaign"), "election"),
    (("whistle","informant","tip-off","expose","leak"), "whistleblower"),
    (("autopsy","morgue","corpse","post-mortem","deceased","body of"), "autopsy"),
    (("crime scene","cordon","scene of","police tape"), "crimescene"),
    (("stage","step","process","journey","first","second","third","final","begin","start","phase"), "steps"),
]
# fallback rotation when a term matches nothing (only slugs that actually exist are used)
TOON_ROT = ["court", "justice", "book", "steps", "documents", "investigation",
            "police", "jail", "money", "city", "office", "lawyer", "arrest",
            "family", "road", "deal", "verdict", "rights"]
# Until every legacy scene has a rebuilt master, map unsupported topics to a
# semantically close HQ plate. Quality stays consistent without showing a random
# or misleading image (for example, a family scene behind a police story).
TOON_HQ_FALLBACK = {
    "arrest": "court", "police": "court", "station": "documents",
    "investigation": "documents", "interrogation": "documents",
    "signature": "documents", "charge": "court", "lawyer": "court",
    "witness": "court", "verdict": "justice", "sentence": "judge",
    "jail": "judge", "bail": "justice", "appeal": "court",
    "money": "documents", "theft": "justice", "weapon": "justice",
    "drugs": "justice", "scam": "documents", "accident": "documents",
    "road": "city", "protest": "rights", "deal": "documents",
    "office": "documents", "clock": "documents", "hospital": "documents",
    "students": "book", "corruption": "justice", "immigration": "malaysia",
    "roadblock": "city", "summons": "documents", "tenancy": "documents",
    "marriage": "family", "inheritance": "family", "employment": "documents",
    "syariah": "family", "customs": "malaysia", "gambling": "justice",
    "drinkdriving": "justice", "vandalism": "justice", "cyber": "documents",
    "defamation": "rights", "fire": "documents", "disaster": "documents",
    "bank": "documents", "insurance": "documents", "airport": "city",
    "cctv": "documents", "oath": "court", "mediation": "justice",
    "seizure": "court", "debt": "documents", "custody": "family",
    "harassment": "rights", "tax": "documents", "company": "city",
    "election": "rights", "whistleblower": "rights", "autopsy": "documents",
    "crimescene": "documents", "steps": "justice",
}
# Each HQ concept has a small pool of compatible alternatives. A fresh random
# seed selects among them per render, so separate videos do not reuse a fixed
# scene order while the imagery remains relevant to the narration.
TOON_HQ_POOLS = {
    "book": ("book", "documents", "justice"),
    "city": ("city", "malaysia", "court"),
    "court": ("court", "judge", "justice", "documents"),
    "documents": ("documents", "book", "court", "justice"),
    "family": ("family", "rights", "court"),
    "judge": ("judge", "court", "justice"),
    "justice": ("justice", "court", "rights", "judge"),
    "malaysia": ("malaysia", "city", "court"),
    "rights": ("rights", "justice", "family", "court"),
}
IMG_EXT = (".jpg", ".jpeg", ".png")

def _is_img(p):
    return bool(p) and p.lower().endswith(IMG_EXT)

def _scene_key(path):
    """Stable repository-relative scene ID (works on Windows and Actions/Linux)."""
    if not path:
        return ""
    try:
        return os.path.relpath(path, HERE).replace("\\", "/")
    except Exception:
        return os.path.basename(path)

def _variants(slug):
    return sorted(glob.glob(os.path.join(LIB, slug + "*.mp4")))

def _toon_action_variants(slug):
    return sorted(glob.glob(os.path.join(TOON_ACTION_DIR, slug + "*.png")))

def _toon_variants(slug):
    # variants are exactly "<slug>.ext" or "<slug><digits>.ext" (e.g. court2.jpg)
    # — NOT any longer word, so slug "road" never grabs "roadblock.jpg"
    # New PNG masters live separately and always win over the legacy compressed
    # JPEG library. This prevents a random seed from selecting a soft duplicate.
    def hq_variants(name):
        found = []
        for p in glob.glob(os.path.join(TOON_HQ_DIR, name + "*.png")):
            rem = os.path.basename(p)[len(name):-4]
            if rem == "" or rem.isdigit():
                found.append(p)
        return sorted(found)

    if glob.glob(os.path.join(TOON_HQ_DIR, "*.png")):
        exact = hq_variants(slug)
        concept = slug if exact else TOON_HQ_FALLBACK.get(slug, "justice")
        pooled = []
        for name in TOON_HQ_POOLS.get(concept, (concept,)):
            pooled.extend(hq_variants(name))
        # Once the rebuilt library exists, never fall back to the visibly
        # softer JPEG sources. The pool also gives every render visual variety.
        return sorted(set(pooled))
    vs = []
    for e in IMG_EXT:
        for p in glob.glob(os.path.join(TOON_DIR, slug + "*" + e)):
            rem = os.path.basename(p)[len(slug):len(os.path.basename(p)) - len(e)]
            if rem == "" or rem.isdigit():
                vs.append(p)
    return sorted(vs)

def toon_available():
    return bool(glob.glob(os.path.join(TOON_ACTION_DIR, "*.png")) or
                glob.glob(os.path.join(TOON_HQ_DIR, "*.png")) or
                glob.glob(os.path.join(TOON_DIR, "*.jpg")) or
                glob.glob(os.path.join(TOON_DIR, "*.png")))

def resolve(term, i, seed=0, avoid=None, toon=False):
    """Map a term to a category, then pick ONE of that category's variants, varying
    by (seed+i) so different videos/lines get different backgrounds. ``avoid`` may
    be one path or a collection of every path already used in the video. In toon
    mode, the variants are cartoon images instead of stock video clips."""
    s = (term or "").lower()
    cat_map = TOON_MAP if toon else LIB_MAP
    pick_variants = _toon_variants if toon else _variants
    rot = TOON_ROT if toon else DEFAULT_ROT
    # collect ALL categories whose keywords hit, in order; pick the first that
    # actually has image files (so a broad category with no art yet doesn't
    # swallow a term whose specific scene DOES exist)
    slug = None
    vs = []
    if toon:
        for kws, sl in TOON_ACTION_MAP:
            if any(k in s for k in kws):
                v = _toon_action_variants(sl)
                if v:
                    slug = sl; vs = v; break
    for kws, sl in cat_map:
        if vs:
            break
        if any(k in s for k in kws):
            v = pick_variants(sl)
            if v:
                slug = sl; vs = v; break
    if not vs:  # unmatched term or no matched category has art → rotate a default
        for d in [rot[(i + seed) % len(rot)]] + rot:
            vs = pick_variants(d)
            if vs: break
    if not vs:
        return None
    if isinstance(avoid, (set, list, tuple)):
        blocked = set(avoid)
    else:
        blocked = {avoid} if avoid else set()
    available = [path for path in vs if path not in blocked]
    if not available and toon and blocked:
        # Prefer an unused HQ plate over repeating a semantic pool. Repetition
        # is allowed only after every available master has appeared once.
        all_hq = sorted(glob.glob(os.path.join(TOON_HQ_DIR, "*.png")))
        available = [path for path in all_hq if path not in blocked]
    if available:
        vs = available
    idx = (i + seed) % len(vs)
    return vs[idx]

# ---- voice + timing ----
def _synth(script, audio):
    async def go():
        c = edge_tts.Communicate(script, VOICE, rate=RATE); sb = []
        with open(audio, "wb") as f:
            async for ch in c.stream():
                if ch["type"] == "audio": f.write(ch["data"])
                elif ch["type"] == "SentenceBoundary":
                    sb.append((ch["text"], ch["offset"]/1e7, ch["duration"]/1e7))
        return sb
    return asyncio.run(go())

BREAK_HARD = (".", "!", "?", ";", ":", "—", "–")
def _chunks(text):
    words = text.split(); g, cur = [], []
    for w in words:
        cur.append(w); wc = w.strip()
        if len(cur) >= 4 or wc.endswith(BREAK_HARD) or (wc.endswith(",") and len(cur) >= 3):
            g.append(" ".join(cur)); cur = []
    if cur: g.append(" ".join(cur))
    return g
def _caps(sentz):
    sem = asyncio.Semaphore(6)
    async def measure(txt):
        async with sem:
            c = edge_tts.Communicate(txt, VOICE, rate=RATE); d = None
            async for ch in c.stream():
                if ch["type"] == "SentenceBoundary": d = ch["duration"]/1e7
            return d if d else max(0.35, 0.045*len(txt))
    async def go():
        out = []
        for text, s0, D in sentz:
            ch = _chunks(text); durs = await asyncio.gather(*(measure(c) for c in ch))
            scale = D / max(0.01, sum(durs)); t = s0
            for c, di in zip(ch, durs):
                out.append([c, t, t + di*scale]); t += di*scale
        return out
    return asyncio.run(go())

def _wrap_caption(text, width=18):
    """Wrap a short caption into at most three balanced, mobile-safe lines."""
    words = (text or "").split()
    if not words:
        return ""
    lines, current = [], []
    for word in words:
        candidate = " ".join(current + [word])
        if current and len(candidate) > width:
            lines.append(" ".join(current)); current = [word]
        else:
            current.append(word)
    if current:
        lines.append(" ".join(current))
    while len(lines) > 3:
        lines[-2:] = [" ".join(lines[-2:])]
    return r"\N".join(lines)

def _caption_markup(text):
    """Give every caption a small kinetic colour accent without changing the art."""
    # Keep the script's punctuation: it carries meaning and makes short caption
    # chunks read naturally instead of looking like disconnected word cards.
    clean = re.sub(r"[{}]", "", (text or "")).strip().upper()
    wrapped = _wrap_caption(clean)
    words = wrapped.split()
    if len(words) < 2:
        return wrapped
    # Highlight the final semantic beat in kampung-gold; reset is explicit so
    # punctuation and following ASS events cannot inherit the colour.
    last = words[-1]
    prefix = " ".join(words[:-1])
    return prefix + r" {\c&H0015CCFA&}" + last + r"{\c&H00FFFFFF&}"

# ---- cartoon host ----
def _remove_bg(path):
    im = Image.open(path).convert("RGB"); w, h = im.size
    seed = im.copy(); SENT = (0, 255, 1)
    for s in [(0,0),(w-1,0),(0,h-1),(w-1,h-1),(w//2,0),(w//2,h-1),(0,h//2),(w-1,h//2)]:
        ImageDraw.floodfill(seed, s, SENT, thresh=45)
    mask = np.all(np.asarray(seed) == np.array(SENT), axis=-1)
    return Image.fromarray(np.dstack([np.asarray(im), np.where(mask, 0, 255).astype("uint8")]), "RGBA")
def _shadow(ch):
    pad = 46
    cv = Image.new("RGBA", (ch.width+pad*2, ch.height+pad*2), (0,0,0,0))
    m = Image.new("L", cv.size, 0); m.paste(ch.split()[3], (pad+8, pad+14))
    m = m.filter(ImageFilter.GaussianBlur(12))
    cv = Image.composite(Image.new("RGBA", cv.size, (0,0,0,165)), cv, m)
    cv.alpha_composite(ch, (pad, pad)); return cv


# Pick the host's mood for a line of narration (first match wins; else neutral).
_EMO = [
    (("death", "died", "dies", " die", "kill", "murder", "loss", "lost", "grief", "tragic", "victim",
      "suffer", "abandon", "cry", "tears", "hurt", "trapped", "helpless", "sadly", "alone"), "sad"),
    (("illegal", "crime", "criminal", "attack", "violence", "assault", "fraud", "abuse", "corrupt",
      "betray", "outrage", "unfair", "injustice", "scam", "threat", "danger", "guilty", "shocking"), "angry"),
    (("win", "won", "victory", "justice", "freedom", "protect", "rights", "safe", "hope", "finally",
      "follow", "made simple", "congrat", "great", "good news"), "happy"),
    (("court", "law", "legal", "statute", "constitution", "ruling", "ruled", "judge", "section",
      "clause", "principle", "evidence", "verdict", "case", "doctrine", "rule", "act"), "serious"),
]
def _emotion(text):
    s = (text or "").lower()
    for kws, e in _EMO:
        if any(k in s for k in kws):
            return e
    return "neutral"

def _normalise_video_sequence(value):
    try:
        value = int(value)
    except (TypeError, ValueError):
        value = secrets.randbits(52)
    return max(1, value)

def _story_beat(index, total, beat_count=5):
    """Map sentences to a small number of longer visual beats.

    The reference video holds one reenactment across the facts and explanation
    of a level. Five beats keeps that pacing while using far fewer source plates
    than changing the background on every short sentence.
    """
    count = max(1, min(int(beat_count), int(total) or 1))
    return min(count - 1, int(index) * count // max(1, int(total)))

def _toon_variant_filter(video_sequence, beat_index, segment_index=0):
    """Return a deterministic, video-specific treatment for one toon plate.

    ``video_sequence`` is allocated persistently by the Telegram webhook. The
    resulting crop, mirror direction, motion path and colour treatment therefore
    cannot be identical between separately queued videos, even after the finite
    source-art library eventually cycles. All operations are native FFmpeg and
    free; no per-video image API is involved.
    """
    seq = _normalise_video_sequence(video_sequence)
    mix = (seq * 1103515245 + (int(beat_index) + 1) * 12345) & 0x7fffffff
    scale_pct = 108 + (mix % 7)                       # 108% .. 114%
    sw = (1080 * scale_pct // 100) // 2 * 2
    sh = (1920 * scale_pct // 100) // 2 * 2
    x_mid = 0.30 + ((mix >> 4) % 401) / 1000.0       # 0.30 .. 0.70
    y_mid = 0.32 + ((mix >> 13) % 361) / 1000.0      # 0.32 .. 0.68
    x_amp = 0.10 + ((mix >> 21) % 81) / 1000.0       # 0.10 .. 0.18
    y_amp = 0.07 + ((mix >> 8) % 61) / 1000.0        # 0.07 .. 0.13
    phase = ((mix % 6283) / 1000.0) + int(segment_index) * 0.71
    direction = -1.0 if (mix & 2) else 1.0
    hue = -5.0 + ((mix >> 6) % 1001) / 100.0         # subtle -5° .. +5°
    sat = 1.01 + ((mix >> 17) % 60) / 1000.0         # 1.01 .. 1.069
    contrast = 1.02 + ((mix >> 11) % 31) / 1000.0    # 1.02 .. 1.05
    brightness = 0.004 + ((mix >> 23) % 13) / 1000.0
    flip = "hflip," if (mix & 1) else ""
    x_delta = direction * x_amp
    x_expr = f"(iw-ow)*({x_mid:.3f}{x_delta:+.3f}*sin(t*0.19+{phase:.3f}))"
    y_expr = f"(ih-oh)*({y_mid:.3f}+{y_amp:.3f}*cos(t*0.16+{phase:.3f}))"
    return (f"{flip}scale={sw}:{sh}:flags=lanczos,"
            f"crop=1080:1920:x='{x_expr}':y='{y_expr}',"
            f"hue=h={hue:.2f}:s={sat:.3f},"
            "unsharp=5:5:0.34:3:3:0.0,"
            f"eq=contrast={contrast:.3f}:brightness={brightness:.3f},"
            "setsar=1,fps=30")

def render(title, script, broll_terms, out_path, progress=None, toon=None,
           video_sequence=None, avoid_backgrounds=None, scene_usage=None):
    def rep(p, label=""):
        if progress:
            try: progress(int(p), label)
            except Exception: pass
    script = (script or "").strip()
    if not script: raise ValueError("empty script")
    # toon=None → auto (cartoon if a lib/toon/ set exists); toon=True/False forces it.
    if toon is None:
        toon = toon_available()
    toon = bool(toon) and toon_available()
    rep(4, "Preparing")
    audio = os.path.join(WORK, "voice.mp3")
    sentz = _synth(script, audio)
    rep(12, "Voiceover ready")

    sd = subprocess.run([FF, "-i", audio, "-af", "silencedetect=noise=-32dB:d=0.12", "-f", "null", "-"],
                        capture_output=True, text=True).stderr
    m = re.search(r"Duration:\s*(\d+):(\d+):([\d.]+)", sd)
    AUD = int(m.group(1))*3600 + int(m.group(2))*60 + float(m.group(3))
    sil = [(float(a), float(b)) for a, b in
           re.findall(r"silence_start:\s*([\d.]+)[\s\S]*?silence_end:\s*([\d.]+)", sd)]
    def speaking(t): return not any(a <= t <= b for a, b in sil)

    rep(16, "Timing captions")
    caps = _caps(sentz)
    for i in range(len(caps)-1): caps[i][2] = caps[i+1][1]
    caps[-1][2] = AUD
    rep(30, "Captions ready")
    def tt(x):
        h=int(x//3600); mn=int(x%3600//60); s=x%60; return f"{h:d}:{mn:02d}:{s:05.2f}"
    ev = [f"Dialogue: 2,{tt(a)},{tt(b)},Main,,0,0,0,,{{\\fad(90,80)}}{_caption_markup(c)}"
          for c, a, b in caps]
    if AUD > 3:
        ev.append(f"Dialogue: 3,{tt(max(0, AUD-2.2))},{tt(AUD)},End,,0,0,0,,{{\\fad(180,220)}}SAVE  ·  SHARE  ·  FOLLOW")
    header = ("[Script Info]\nScriptType: v4.00+\nPlayResX: 1080\nPlayResY: 1920\n\n"
    "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
    "Style: Main,Arial,82,&H00FFFFFF,&H000000FF,&H00181B24,&H70000000,-1,0,0,0,100,100,0,0,1,7,2,8,82,82,305,1\n"
    "Style: End,Arial,46,&H0015CCFA,&H000000FF,&H00181B24,&H90000000,-1,0,0,0,100,100,3,0,3,2,0,2,70,70,74,1\n\n"
    "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n")
    open(os.path.join(WORK, "subs.ass"), "w", encoding="utf-8").write(header + "\n".join(ev) + "\n")

    def _prep_pair(closed_path, open_path, size=None):
        Cr, Or = _remove_bg(closed_path), _remove_bg(open_path)
        b1, b2 = Cr.split()[3].getbbox(), Or.split()[3].getbbox()
        bx = (min(b1[0], b2[0]), min(b1[1], b2[1]), max(b1[2], b2[2]), max(b1[3], b2[3]))
        w0, h0 = bx[2]-bx[0], bx[3]-bx[1]; s0 = 560 / w0
        cc = _shadow(Cr.crop(bx).resize((int(w0*s0), int(h0*s0))))
        oo = _shadow(Or.crop(bx).resize((int(w0*s0), int(h0*s0))))
        if size:
            cc, oo = cc.resize(size), oo.resize(size)
        return cc, oo

    C, O = _prep_pair(os.path.join(HERE, "kampung_clean.png"), os.path.join(HERE, "kampung_open3.png"))
    HEAD_W, HEAD_H = C.size
    # Per-emotion host pairs — drop <emotion>_closed.png + <emotion>_open.png into
    # lib/emotions/ (sad, serious, angry; happy defaults to the smiling host).
    # Missing ones simply fall back to the default host, so this never breaks.
    EMO_DIR = os.path.join(LIB, "emotions")
    PAIRS = {"neutral": (C, O), "happy": (C, O)}
    for e in ("sad", "serious", "angry"):
        cp, op = os.path.join(EMO_DIR, f"{e}_closed.png"), os.path.join(EMO_DIR, f"{e}_open.png")
        PAIRS[e] = _prep_pair(cp, op, size=(HEAD_W, HEAD_H)) if (os.path.exists(cp) and os.path.exists(op)) else (C, O)

    def _emotion_at(t):
        idx = 0
        for i in range(len(sentz)):
            if sentz[i][1] <= t: idx = i
            else: break
        return _emotion(sentz[idx][0]) if sentz else "neutral"

    for f in os.listdir(WORK):
        if f.startswith("h") and f.endswith(".png"): os.remove(os.path.join(WORK, f))
    nf = int(AUD*HFPS)+1
    for k in range(nf):
        t = k / HFPS
        Ce, Oe = PAIRS.get(_emotion_at(t), (C, O))
        # A four-frame cycle stays natural but tracks fast captioned speech more
        # closely than the slower five-frame version.
        mouth_open = speaking(t) and (k % 4) in (1, 2)
        (Oe if mouth_open else Ce).save(os.path.join(WORK, f"h{k:04d}.png"))
        if k % 12 == 0: rep(30 + 34*k/max(1, nf), "Animating host")
    subprocess.run([FF, "-y", "-framerate", str(HFPS), "-i", "h%04d.png", "-c:v", "qtrle", "head.mov",
                    "-loglevel", "error"], cwd=WORK)
    rep(66, "Host ready")

    starts = [0.0] + [sentz[i][1] for i in range(1, len(sentz))]
    ends = starts[1:] + [AUD]
    terms = broll_terms or []
    sequence = _normalise_video_sequence(video_sequence)
    # The persistent sequence drives both source selection and visual treatment.
    # Unlike a random seed, it cannot accidentally reproduce an earlier job.
    seed = (sequence * 2654435761) & 0x7fffffff
    prior_keys = {str(x).replace("\\", "/") for x in (avoid_backgrounds or []) if x}
    candidate_paths = (glob.glob(os.path.join(TOON_ACTION_DIR, "*.png")) +
                       glob.glob(os.path.join(TOON_HQ_DIR, "*.png")))
    prior_scenes = {p for p in candidate_paths if _scene_key(p) in prior_keys}
    last = None; used_scenes = set(); lines = []

    beat_count = min(5, len(sentz)) if toon else len(sentz)
    beat_sources = {}
    if toon:
        # Select one relevant plate for each story beat. Combining the b-roll
        # terms in a beat lets a concrete action (for example "one punch") win
        # over a nearby generic legal-analysis term.
        for beat in range(beat_count):
            members = [j for j in range(len(sentz))
                       if _story_beat(j, len(sentz), beat_count) == beat]
            combined = " ".join(
                (terms[j] if j < len(terms) else sentz[j][0]) for j in members
            )
            blocked = prior_scenes | used_scenes
            src = resolve(combined, beat, seed, avoid=blocked, toon=True) or last
            if src:
                last = src
                used_scenes.add(src)
            beat_sources[beat] = src

        if scene_usage is not None:
            scene_usage[:] = sorted(_scene_key(p) for p in used_scenes if p)

    for i in range(len(sentz)):
        term = terms[i] if i < len(terms) else sentz[i][0]
        if toon:  # cartoon mode: bundled doodle images only (no live stock footage)
            beat = _story_beat(i, len(sentz), beat_count)
            src = beat_sources.get(beat) or last
        else:
            src = _pexels_fetch(term, i + seed) or resolve(term, i, seed, avoid=last) or last
        if src:
            last = src
        dur = max(0.6, ends[i]-starts[i]); seg = os.path.join(WORK, f"s{i:02d}.mp4")
        if src and _is_img(src):   # static cartoon background
            inp = ["-loop", "1", "-i", src]
            beat = _story_beat(i, len(sentz), beat_count)
            vf = _toon_variant_filter(sequence, beat, i)
        elif src:
            inp = ["-stream_loop", "-1", "-i", src]
            phase = (i % 4) * 0.75
            vf = ("scale=1120:1992:force_original_aspect_ratio=increase,"
                  f"crop=1080:1920:x='(iw-ow)/2+12*sin(t*0.24+{phase})':"
                  f"y='(ih-oh)/2+10*cos(t*0.20+{phase})',"
                  "unsharp=5:5:0.32:3:3:0.0,"
                  "eq=saturation=1.035:contrast=1.02:brightness=0.01,setsar=1,fps=30")
        else:  # gradient fallback (should be rare)
            inp = ["-f", "lavfi", "-i", "color=c=0x12162e:s=1080x1920:r=30"]
            vf = "setsar=1"
        subprocess.run([FF, "-y", *inp, "-t", f"{dur:.3f}", "-vf", vf,
            "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "slow", "-tune", "animation", "-crf", "12",
            seg, "-loglevel", "error"])
        lines.append(f"file 's{i:02d}.mp4'")
        rep(66 + 26*(i+1)/max(1, len(sentz)), "Building backgrounds")
    open(os.path.join(WORK, "list.txt"), "w").write("\n".join(lines))
    subprocess.run([FF, "-y", "-f", "concat", "-safe", "0", "-i", "list.txt", "-c", "copy", "bg.mp4",
                    "-loglevel", "error"], cwd=WORK)
    rep(94, "Stitching")

    oy = 1920 - HEAD_H - 30
    rep(96, "Adding captions & audio")
    subprocess.run([FF, "-y", "-i", "bg.mp4", "-i", os.path.join(WORK, "head.mov"), "-i", audio,
        "-filter_complex", (f"[0:v][1:v]overlay=x='8+4*sin(t*2.2)':y='{oy}+5*sin(t*2.8)':shortest=1,ass=subs.ass[v];"
                            "[2:a]highpass=f=75,acompressor=threshold=-18dB:ratio=3:attack=18:release=220,"
                            "loudnorm=I=-16:TP=-1.5:LRA=7[a]"),
        "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-preset", "slow", "-tune", "animation", "-crf", "12", "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
        "-movflags", "+faststart", "-shortest", out_path, "-loglevel", "error"], cwd=WORK)
    rep(100, "Done")
    return out_path
