// api/pexels.js — Pexels proxy (CommonJS, no deps).
//
// The GitHub Actions render can't reach Pexels' website (datacenter IPs get
// blocked) and can't hold the API key (workflow env is locked). So the render
// asks THIS Vercel function, which holds the key in env and returns a portrait
// video URL for a search term. The render then downloads that URL from Pexels'
// CDN (which is not blocked). Falls back to the bundled clip library if unset.
//
// Env var on Vercel: PEXELS_API_KEY  (free key from https://www.pexels.com/api/)

const KEY = process.env.PEXELS_API_KEY;

module.exports = async (req, res) => {
  const url = new URL(req.url, `https://${req.headers.host}`);
  const q = (url.searchParams.get('q') || '').trim();
  const i = parseInt(url.searchParams.get('i') || '0', 10) || 0;
  if (!KEY || !q) { res.status(200).json({ url: null }); return; }
  try {
    const r = await fetch(
      `https://api.pexels.com/videos/search?query=${encodeURIComponent(q)}&orientation=portrait&per_page=15&size=medium`,
      { headers: { Authorization: KEY } });
    if (!r.ok) { res.status(200).json({ url: null }); return; }
    const j = await r.json();
    const vids = j.videos || [];
    if (!vids.length) { res.status(200).json({ url: null }); return; }
    const v = vids[((i % vids.length) + vids.length) % vids.length];   // vary pick per line/video
    let files = (v.video_files || []).filter((f) => f.height && f.width && f.height > f.width);
    if (!files.length) files = v.video_files || [];
    // prefer a file whose width is closest to 1080 (good for a 1080x1920 crop)
    files.sort((a, b) => Math.abs((a.width || 0) - 1080) - Math.abs((b.width || 0) - 1080));
    const pick = files[0];
    res.status(200).json({ url: pick ? pick.link : null });
  } catch (e) {
    res.status(200).json({ url: null });
  }
};
