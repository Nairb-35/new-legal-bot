"""
video_maker.py - renders a vertical law short from (title, script, broll_terms).
Runs on the GitHub Actions runner (called by bilingual_news.py). Uses the bundled
`lib/` background clips (keyword-matched) so it never depends on a live stock site.
Deps (edge-tts, imageio-ffmpeg, pillow, numpy) are installed on demand by the caller.
"""
import asyncio, os, subprocess, re
import numpy as np
import edge_tts, imageio_ffmpeg
from PIL import Image, ImageDraw, ImageFilter

HERE = os.path.dirname(os.path.abspath(__file__))
LIB = os.path.join(HERE, "lib")
WORK = os.path.join(HERE, "_vwork")
os.makedirs(WORK, exist_ok=True)
FF = imageio_ffmpeg.get_ffmpeg_exe()
VOICE, RATE = "en-US-AndrewNeural", "+8%"
HFPS = 15

# ---- map an AI b-roll term to one of the bundled library clips ----
LIB_MAP = [
    (("sign","signature","contract","document","paper","form","pen"), "signing"),
    (("court","judge","gavel","ruling","ruled","tribunal","verdict","judicial","trial","lawsuit"), "court"),
    (("parliament","minister","government","cabinet","ministry","official","policy","authority","department","office"), "building"),
    (("stair","climb","journey","rise","steps","struggle","fight all"), "stairs"),
    (("protest","rally","crowd","demonstration","march","riot"), "protest"),
    (("deal","agreement","handshake","business","company","corporate","negotiat","meeting"), "handshake"),
    (("family","child","kid","home","parent","together","baby","custody"), "family"),
    (("mother","woman","man","person","worried","sad","think","alone","victim","people","citizen"), "person"),
    (("city","skyline","kuala","urban","town","night","street","building"), "city"),
    (("book","law","read","word","letter","constitution","statute","act","clause","interpret","rule","page","fine print"), "book"),
]
DEFAULT_ROT = ["court", "building", "book", "city"]
def resolve(term, i):
    s = (term or "").lower()
    for kws, slug in LIB_MAP:
        if any(k in s for k in kws):
            p = os.path.join(LIB, slug + ".mp4")
            if os.path.exists(p): return p
    p = os.path.join(LIB, DEFAULT_ROT[i % len(DEFAULT_ROT)] + ".mp4")
    return p if os.path.exists(p) else None

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

def render(title, script, broll_terms, out_path, progress=None):
    def rep(p, label=""):
        if progress:
            try: progress(int(p), label)
            except Exception: pass
    script = (script or "").strip()
    if not script: raise ValueError("empty script")
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
    def disp(t): return t.replace("{","").replace("}","").strip().strip(",.—– ").upper()
    def tt(x):
        h=int(x//3600); mn=int(x%3600//60); s=x%60; return f"{h:d}:{mn:02d}:{s:05.2f}"
    ev = [f"Dialogue: 0,{tt(a)},{tt(b)},Main,,0,0,0,,{disp(c)}" for c, a, b in caps]
    header = ("[Script Info]\nScriptType: v4.00+\nPlayResX: 1080\nPlayResY: 1920\n\n"
    "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
    "Style: Main,Arial,100,&H0000FFFF,&H000000FF,&H00000000,&H90000000,-1,0,0,0,100,100,0,0,1,8,4,2,70,70,780,1\n\n"
    "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n")
    open(os.path.join(WORK, "subs.ass"), "w", encoding="utf-8").write(header + "\n".join(ev) + "\n")

    Craw, Oraw = _remove_bg(os.path.join(HERE, "kampung_clean.png")), _remove_bg(os.path.join(HERE, "kampung_open2.png"))
    b1, b2 = Craw.split()[3].getbbox(), Oraw.split()[3].getbbox()
    box = (min(b1[0],b2[0]), min(b1[1],b2[1]), max(b1[2],b2[2]), max(b1[3],b2[3]))
    bw, bh = box[2]-box[0], box[3]-box[1]; sc = 560 / bw
    def prep(r): return _shadow(r.crop(box).resize((int(bw*sc), int(bh*sc))))
    C, O = prep(Craw), prep(Oraw); HEAD_W, HEAD_H = C.size
    for f in os.listdir(WORK):
        if f.startswith("h") and f.endswith(".png"): os.remove(os.path.join(WORK, f))
    nf = int(AUD*HFPS)+1
    for k in range(nf):
        (O if (speaking(k/HFPS) and k % 2 == 0) else C).save(os.path.join(WORK, f"h{k:04d}.png"))
        if k % 12 == 0: rep(30 + 34*k/max(1, nf), "Animating host")
    subprocess.run([FF, "-y", "-framerate", str(HFPS), "-i", "h%04d.png", "-c:v", "qtrle", "head.mov",
                    "-loglevel", "error"], cwd=WORK)
    rep(66, "Host ready")

    starts = [0.0] + [sentz[i][1] for i in range(1, len(sentz))]
    ends = starts[1:] + [AUD]
    terms = broll_terms or []
    last = None; lines = []
    for i in range(len(sentz)):
        term = terms[i] if i < len(terms) else sentz[i][0]
        src = resolve(term, i) or last
        if src: last = src
        dur = max(0.6, ends[i]-starts[i]); seg = os.path.join(WORK, f"s{i:02d}.mp4")
        if src:
            inp = ["-stream_loop", "-1", "-i", src]
            vf = "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1,fps=30"
        else:  # gradient fallback (should be rare)
            inp = ["-f", "lavfi", "-i", "color=c=0x12162e:s=1080x1920:r=30"]
            vf = "setsar=1"
        subprocess.run([FF, "-y", *inp, "-t", f"{dur:.3f}", "-vf", vf,
            "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "veryfast", seg, "-loglevel", "error"])
        lines.append(f"file 's{i:02d}.mp4'")
        rep(66 + 26*(i+1)/max(1, len(sentz)), "Building backgrounds")
    open(os.path.join(WORK, "list.txt"), "w").write("\n".join(lines))
    subprocess.run([FF, "-y", "-f", "concat", "-safe", "0", "-i", "list.txt", "-c", "copy", "bg.mp4",
                    "-loglevel", "error"], cwd=WORK)
    rep(94, "Stitching")

    oy = 1920 - HEAD_H - 30
    rep(96, "Adding captions & audio")
    subprocess.run([FF, "-y", "-i", "bg.mp4", "-i", os.path.join(WORK, "head.mov"), "-i", audio,
        "-filter_complex", f"[0:v][1:v]overlay=8:{oy}:shortest=1,ass=subs.ass[v]",
        "-map", "[v]", "-map", "2:a", "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "160k", "-shortest", out_path, "-loglevel", "error"], cwd=WORK)
    rep(100, "Done")
    return out_path
