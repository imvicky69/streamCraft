import os
import sys
import re
import json
import time
import uuid
import random
import base64
import tempfile
import zipfile
import shutil
import urllib.parse
import subprocess
from typing import Optional, List, Dict
from fastapi import FastAPI, HTTPException, Query, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, FileResponse
from pydantic import BaseModel

# ── Auto-update yt-dlp at startup (always use latest bypass patches) ──────────
def _auto_update_ytdlp():
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", "--upgrade", "--quiet", "yt-dlp"],
            capture_output=True, text=True, timeout=60
        )
        if result.returncode == 0:
            print("[startup] yt-dlp upgraded successfully")
        else:
            print(f"[startup] yt-dlp upgrade warning: {result.stderr[:200]}")
    except Exception as e:
        print(f"[startup] yt-dlp upgrade skipped: {e}")

_auto_update_ytdlp()

# Enable static-ffmpeg if installed
try:
    import static_ffmpeg
    static_ffmpeg.add_paths()
except Exception:
    pass

# Try yt-dlp
try:
    import yt_dlp
    HAS_YTDLP = True
except ImportError:
    HAS_YTDLP = False

# httpx for Invidious / Innertube fallback
try:
    import httpx
    HAS_HTTPX = True
except ImportError:
    HAS_HTTPX = False

# ── Proxy configuration ────────────────────────────────────────────────────────
# Set PROXY_URL in Render → Environment Variables.
# Format: http://user:pass@host:port  OR  socks5://user:pass@host:port
# Free residential proxies: webshare.io (10 free), proxyscrape.com
PROXY_URL: Optional[str] = (
    os.environ.get("PROXY_URL") or
    os.environ.get("HTTPS_PROXY") or
    os.environ.get("HTTP_PROXY") or
    None
)

# ── PO Token (Proof of Origin — bypasses bot-detection on datacenter IPs) ─────
# Set PO_TOKEN in Render → Environment Variables.
# Generate: https://github.com/YunzheZJU/youtube-po-token-generator
# Or manually from yt-dlp wiki: open youtube.com in browser and extract token.
PO_TOKEN: Optional[str] = os.environ.get("PO_TOKEN") or None

# ── Invidious public instances (cookie-free YouTube API) ──────────────────────
# Shuffled randomly on each request to avoid hammering one instance.
INVIDIOUS_INSTANCES = [
    "https://yewtu.be",
    "https://inv.tux.pizza",
    "https://invidious.privacyredirect.com",
    "https://invidious.io",
    "https://inv.us.projectsegfault.net",
    "https://invidious.slipfox.xyz",
    "https://invidious.fdn.fr",
    "https://invidious.nerdvpn.de",
    "https://invidious.incogniweb.net",
    "https://iv.ggtyler.dev",
    "https://invidious.perennialte.ch",
    "https://inv.bp.projectsegfault.net",
]

app = FastAPI(
    title="YouTube & YouTube Music Downloader API",
    description="FastAPI backend for video info, MP3 conversion, and live SSE playlist ZIP downloads",
    version="1.0.0"
)

# Allow all origins in development; on production Render serves both frontend proxied via Vercel
# and direct browser requests, so we keep '*' for maximum compatibility.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)

# In-memory store for generated zip files waiting to be downloaded
ZIP_STORAGE: Dict[str, Dict] = {}


class VideoInfoRequest(BaseModel):
    url: str


def format_size(bytes_size: Optional[int], is_approx: bool = False) -> str:
    """Format bytes into readable string."""
    if not bytes_size or bytes_size <= 0:
        return "Unknown size"
    size = float(bytes_size)
    prefix = "~" if is_approx else ""
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size < 1024.0:
            return f"{prefix}{size:.1f} {unit}"
        size /= 1024.0
    return f"{prefix}{size:.1f} TB"


def format_duration(seconds: Optional[int]) -> str:
    """Format seconds into HH:MM:SS or MM:SS."""
    if not seconds:
        return "0:00"
    mins, secs = divmod(int(seconds), 60)
    hours, mins = divmod(mins, 60)
    if hours > 0:
        return f"{hours:02d}:{mins:02d}:{secs:02d}"
    return f"{mins:02d}:{secs:02d}"


def clean_url(url: str) -> str:
    """Normalize URL, transform music.youtube.com & shorts, fix missing https://."""
    url = url.strip()
    url = re.sub(r'\s+', '', url)

    if not url.startswith('http://') and not url.startswith('https://'):
        url = 'https://' + url

    # Transform music.youtube.com to standard youtube.com
    url = url.replace('music.youtube.com', 'www.youtube.com')

    # Transform shorts to watch
    if '/shorts/' in url:
        url = url.replace('/shorts/', '/watch?v=')

    # Transform youtu.be to watch
    if 'youtu.be/' in url:
        parts = url.split('youtu.be/')[1].split('?')[0]
        url = f'https://www.youtube.com/watch?v={parts}'

    # If it's a single watch video URL, strip radio mix / index parameters
    if 'watch?v=' in url and '&list=' in url:
        url = url.split('&list=')[0]
    if 'watch?v=' in url and '&index=' in url:
        url = url.split('&index=')[0]

    # Clean trailing ampersands
    url = url.rstrip('&?')

    return url


def sanitize_filename(name: str) -> str:
    """Make filename safe for filesystem and ASCII HTTP headers (strip emojis/non-ascii)."""
    clean = re.sub(r'[\\/*?:"<>|]', '', name)
    ascii_clean = clean.encode('ascii', 'ignore').decode('ascii').strip()
    return ascii_clean or "media"


COOKIE_FILES: List[str] = []


def clean_netscape_cookies(raw_text: str) -> str:
    """Preserve ALL YouTube cookies — do NOT strip anti-bot cookies.
    YouTube uses VISITOR_INFO1_LIVE, YSC, CONSISTENCY etc for bot checks.
    Removing them is what causes the 'Sign in to confirm' error on cloud IPs.
    """
    lines = raw_text.splitlines()
    clean_lines = ['# Netscape HTTP Cookie File']
    for l in lines:
        if not l.strip():
            continue
        # Keep comment header lines
        if l.startswith('#'):
            if 'Netscape' in l or 'yt-dlp' in l or 'Do not edit' in l:
                continue  # skip duplicate headers
            continue
        parts = l.strip().split('\t')
        if len(parts) >= 7:
            clean_lines.append(l)
    return '\n'.join(clean_lines) + '\n'


def init_cookie_pool():
    """Discover, sanitize, and initialize multiple cookie accounts from env variables and files."""
    global COOKIE_FILES
    COOKIE_FILES = []

    # 1. Local cookie files
    candidate_paths = [
        'cookie.txt', 'cookies.txt', 'youtube_cookies.txt', '/tmp/cookies.txt',
        'cookies_1.txt', 'cookies_2.txt',
        os.path.join(os.path.dirname(__file__), '..', 'cookie.txt'),
        os.path.join(os.path.dirname(__file__), '..', 'cookies.txt'),
        os.path.join(os.path.dirname(__file__), 'cookie.txt'),
    ]

    for local_file in candidate_paths:
        if os.path.exists(local_file):
            try:
                with open(local_file, 'r', encoding='utf-8', errors='ignore') as f:
                    content = f.read().strip()
                if content:
                    cleaned = clean_netscape_cookies(content)
                    tf = tempfile.NamedTemporaryFile(delete=False, suffix='_cleaned_cookie.txt', mode='w', encoding='utf-8')
                    tf.write(cleaned)
                    tf.close()
                    if tf.name not in COOKIE_FILES:
                        COOKIE_FILES.append(tf.name)
            except Exception as e:
                print(f"Notice: Failed to load local cookie {local_file}: {e}")

    # 2. Environment variables: YOUTUBE_COOKIES, YOUTUBE_COOKIES_1, YOUTUBE_COOKIES_2, etc.
    cookie_entries = []
    for k, v in os.environ.items():
        if (k.startswith('YOUTUBE_COOKIE') or k.startswith('YT_COOKIE') or k == 'COOKIES') and v.strip():
            if '===COOKIE===' in v:
                cookie_entries.extend([part.strip() for part in v.split('===COOKIE===') if part.strip()])
            else:
                cookie_entries.append(v.strip())

    for idx, raw_cookie in enumerate(cookie_entries):
        try:
            import base64
            if not raw_cookie.startswith('# Netscape') and '\t' not in raw_cookie:
                try:
                    decoded = base64.b64decode(raw_cookie).decode('utf-8')
                    if '# Netscape' in decoded or '\t' in decoded:
                        raw_cookie = decoded
                except Exception:
                    pass

            cleaned = clean_netscape_cookies(raw_cookie)
            f = tempfile.NamedTemporaryFile(delete=False, suffix=f'_yt_cookie_{idx}.txt', mode='w', encoding='utf-8')
            f.write(cleaned)
            f.close()
            COOKIE_FILES.append(f.name)
        except Exception as e:
            print(f"Notice: Failed to load cookie #{idx}: {e}")


# Initialize pool at startup
init_cookie_pool()


def get_base_ydl_opts(cookie_idx: Optional[int] = None) -> dict:
    """
    Build yt-dlp options with:
    - Cookie pool rotation
    - Proxy support (PROXY_URL env var)
    - PO token support (PO_TOKEN env var)
    """
    global COOKIE_FILES
    if not COOKIE_FILES:
        init_cookie_pool()

    cookie_file_path = None
    if COOKIE_FILES:
        if cookie_idx is not None and 0 <= cookie_idx < len(COOKIE_FILES):
            cookie_file_path = COOKIE_FILES[cookie_idx]
        else:
            cookie_file_path = random.choice(COOKIE_FILES)

    extractor_args: dict = {'youtube': {}}

    # When proxy is present, android + web clients fetch full DASH adaptive streams (1080p, 720p, etc.)
    if PO_TOKEN:
        extractor_args['youtube']['po_token'] = [f'web+{PO_TOKEN}']
        extractor_args['youtube']['player_client'] = ['web', 'mweb', 'android']
    elif PROXY_URL:
        extractor_args['youtube']['player_client'] = ['android', 'web', 'mweb']
    elif cookie_file_path:
        extractor_args['youtube']['player_client'] = ['tv_embedded', 'web_embedded', 'mweb', 'android', 'web']
    else:
        extractor_args['youtube']['player_client'] = ['tv_embedded', 'web_embedded', 'mweb']

    opts: dict = {
        'quiet': True,
        'no_warnings': True,
        'socket_timeout': 15,
        'retries': 3,
        'fragment_retries': 3,
        'concurrent_fragment_downloads': 5,   # 5x parallel downloading for maximum speed over proxy
        'buffersize': 1024 * 1024,
        'http_chunk_size': 10485760,          # 10MB chunked downloads
        'nocheckcertificate': True,
        'extractor_args': extractor_args,
        'http_headers': {
            'User-Agent': (
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                'AppleWebKit/537.36 (KHTML, like Gecko) '
                'Chrome/128.0.0.0 Safari/537.36'
            ),
            'Accept-Language': 'en-US,en;q=0.9',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        },
        'geo_bypass': True,
        'geo_bypass_country': 'US',
    }

    if cookie_file_path:
        opts['cookiefile'] = cookie_file_path

    if PROXY_URL:
        opts['proxy'] = PROXY_URL

    return opts


def _ytdlp_extract_info(url: str) -> Optional[dict]:
    """
    Try yt-dlp with multiple strategies:
    1. If proxy is present: Use proxy with standard clients & cookies
    2. Default yt-dlp extractor settings
    3. tv_embedded / web_embedded clients fallback
    Returns None if all fail so fallbacks (Innertube/Invidious) can run.
    """
    if not HAS_YTDLP:
        return None

    base_extract_opts = {'skip_download': True, 'extract_flat': 'in_playlist'}
    strategies = []

    # Strategy 1: Standard with configured options
    s1 = get_base_ydl_opts()
    s1.update(base_extract_opts)
    strategies.append(("primary", s1))

    # Strategy 2: Default yt-dlp player clients (no extractor_args override)
    s2 = get_base_ydl_opts()
    s2.pop('extractor_args', None)
    s2.update(base_extract_opts)
    strategies.append(("default-clients", s2))

    # Strategy 3: TV client without cookies
    s3 = get_base_ydl_opts()
    s3.pop('cookiefile', None)
    s3['extractor_args'] = {
        'youtube': {'player_client': ['tv_embedded', 'web_embedded', 'mweb']}
    }
    s3.update(base_extract_opts)
    strategies.append(("tv-embedded-no-cookie", s3))

    for strategy_name, opts in strategies:
        try:
            print(f"[yt-dlp] Trying strategy: {strategy_name}")
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False)
            if info:
                print(f"[yt-dlp] Success with strategy: {strategy_name}")
                return info
        except Exception as e:
            err_msg = str(e)
            print(f"[yt-dlp] Strategy '{strategy_name}' failed: {err_msg[:120]}")
            if "Video unavailable" in err_msg or "Private video" in err_msg or "removed by the uploader" in err_msg:
                raise HTTPException(status_code=400, detail="This video is unavailable or private.")
            # Format/client/bot error -> try next strategy or fallback
            continue

    return None  # All strategies exhausted — proceed to Innertube/Invidious fallbacks


@app.get("/api/health")
def health():
    return {
        "status": "ok",
        "engine": "yt-dlp" if HAS_YTDLP else "none",
        "fallback_engine": "innertube+invidious" if HAS_HTTPX else "none",
        "cookies_loaded": len(COOKIE_FILES),
        "proxy_configured": bool(PROXY_URL),
        "po_token_configured": bool(PO_TOKEN),
        "message": "Downloader API is operational"
    }


@app.get("/api/debug")
def debug():
    """Test all Invidious instances and report connectivity from this server."""
    results = {}
    TEST_VIDEO = "dQw4w9WgXcQ"  # Rick Astley — always public
    if HAS_HTTPX:
        proxy_kwargs = {"proxy": PROXY_URL} if PROXY_URL else {}
        for instance in INVIDIOUS_INSTANCES:
            try:
                url = f"{instance}/api/v1/videos/{TEST_VIDEO}?fields=title"
                resp = httpx.get(url, timeout=8, follow_redirects=True, **proxy_kwargs)
                results[instance] = {
                    "status": resp.status_code,
                    "ok": resp.status_code == 200,
                    "title": resp.json().get("title", "N/A") if resp.status_code == 200 else None
                }
            except Exception as e:
                results[instance] = {"status": "error", "ok": False, "error": str(e)[:80]}
    else:
        results["error"] = "httpx not installed"

    ytdlp_version = "not installed"
    if HAS_YTDLP:
        try:
            ytdlp_version = yt_dlp.version.__version__
        except Exception:
            ytdlp_version = "unknown"

    return {
        "invidious_instances": results,
        "working_instances": [k for k, v in results.items() if isinstance(v, dict) and v.get("ok")],
        "yt_dlp_version": ytdlp_version,
        "cookies_loaded": len(COOKIE_FILES),
        "proxy_configured": bool(PROXY_URL),
        "po_token_configured": bool(PO_TOKEN),
    }


# ── Cookie-free fallback engines ──────────────────────────────────────────────

def extract_video_id(url: str) -> Optional[str]:
    """Extract YouTube video ID from any URL format."""
    m = re.search(r'(?:v=|youtu\.be/|/embed/|/v/|/watch\?v=)([A-Za-z0-9_-]{11})', url)
    return m.group(1) if m else None


YT_INNERTUBE_CLIENTS = [
    # Smart TV embedded — YouTube uses for smart TVs on ISP networks, barely flagged on cloud IPs
    {
        "clientName": "TVHTML5_SIMPLY_EMBEDDED_PLAYER",
        "clientVersion": "2.0",
        "key": "AIzaSyAO_FJ2SlqU8Q4STEHLGCilw_Y9_11qcW8",
        "user_agent": "Mozilla/5.0 (SMART-TV; LINUX; Tizen 5.5) AppleWebKit/537.36",
    },
    # Android VR — headset client, very low datacenter detection
    {
        "clientName": "ANDROID_VR",
        "clientVersion": "1.56.21",
        "androidSdkVersion": 32,
        "key": "AIzaSyAO_FJ2SlqU8Q4STEHLGCilw_Y9_11qcW8",
        "user_agent": "com.google.android.apps.youtube.vr.oculus/1.56.21 (Linux; U; Android 12) gzip",
    },
    # iOS Messages Extension — secondary embedded client
    {
        "clientName": "IOS_MESSAGES_EXTENSION",
        "clientVersion": "19.29.1",
        "deviceModel": "iPhone16,2",
        "osVersion": "17.5.1.21F90",
        "key": "AIzaSyB-63vPrdThhKuerbB2N_l7Kwwcxj6yUAc",
        "user_agent": "com.google.ios.youtube/19.29.1 (iPhone16,2; U; CPU iOS 17_5_1 like Mac OS X)",
    },
    # Android Music — rarely flagged, separate app client
    {
        "clientName": "ANDROID_MUSIC",
        "clientVersion": "7.27.52",
        "androidSdkVersion": 30,
        "key": "AIzaSyAOghZGza2MQSZkY_zfZ370N-PUdXEo8AI",
        "user_agent": "com.google.android.apps.youtube.music/7.27.52 (Linux; U; Android 11) gzip",
    },
    # Web embedded player — last resort Innertube attempt
    {
        "clientName": "WEB_EMBEDDED_PLAYER",
        "clientVersion": "2.20240101.00.00",
        "key": "AIzaSyAO_FJ2SlqU8Q4STEHLGCilw_Y9_11qcW8",
        "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    },
]


def innertube_get_info(video_id: str) -> Optional[dict]:
    """Call YouTube's internal Innertube API — no cookies, minimal bot detection."""
    if not HAS_HTTPX:
        return None

    proxy_kwargs = {"proxy": PROXY_URL} if PROXY_URL else {}

    for client_cfg in YT_INNERTUBE_CLIENTS:
        try:
            key = client_cfg["key"]
            api_url = f"https://www.youtube.com/youtubei/v1/player?key={key}&prettyPrint=false"

            ctx: dict = {
                "clientName": client_cfg["clientName"],
                "clientVersion": client_cfg["clientVersion"],
                "hl": "en",
                "gl": "US",
                "utcOffsetMinutes": 0,
            }
            if "androidSdkVersion" in client_cfg:
                ctx["androidSdkVersion"] = client_cfg["androidSdkVersion"]
            if "deviceModel" in client_cfg:
                ctx["deviceModel"] = client_cfg["deviceModel"]
            if "osVersion" in client_cfg:
                ctx["osVersion"] = client_cfg["osVersion"]

            payload = {
                "videoId": video_id,
                "context": {"client": ctx},
                "racyCheckOk": True,
                "contentCheckOk": True,
                # PO token hint (helps on blocked IPs when configured)
                "serviceIntegrityDimensions": {"poToken": PO_TOKEN} if PO_TOKEN else {},
            }

            resp = httpx.post(
                api_url, json=payload, timeout=8,
                headers={
                    "Content-Type": "application/json",
                    "User-Agent": client_cfg["user_agent"],
                    "Origin": "https://www.youtube.com",
                    "Referer": f"https://www.youtube.com/watch?v={video_id}",
                    "X-YouTube-Client-Name": client_cfg["clientName"],
                    "X-YouTube-Client-Version": client_cfg["clientVersion"],
                },
                **proxy_kwargs
            )

            if resp.status_code != 200:
                print(f"Innertube {client_cfg['clientName']} returned {resp.status_code}")
                continue

            data = resp.json()
            status = data.get("playabilityStatus", {}).get("status", "")
            if status == "OK":
                print(f"Innertube success with {client_cfg['clientName']}")
                return data
            print(f"Innertube {client_cfg['clientName']}: playabilityStatus={status}")

        except Exception as e:
            print(f"Innertube {client_cfg['clientName']} failed: {e}")
            continue

    return None


def innertube_to_response(data: dict, video_id: str) -> dict:
    """Convert Innertube player response to our standard API format."""
    details = data.get("videoDetails", {})
    title = details.get("title", "Unknown Title")
    author = details.get("author", "Unknown")
    duration = int(details.get("lengthSeconds", 0))
    views = int(details.get("viewCount", 0))
    thumbnail_url = f"https://i.ytimg.com/vi/{video_id}/maxresdefault.jpg"

    streaming = data.get("streamingData", {})
    adaptive = streaming.get("adaptiveFormats", [])
    combined = streaming.get("formats", [])

    seen_res = set()
    video_streams = []
    for f in adaptive + combined:
        height = f.get("height")
        if not height:
            continue
        mime = f.get("mimeType", "")
        if "video" not in mime:
            continue
        res_str = f"{height}p"
        if res_str not in seen_res:
            seen_res.add(res_str)
            filesize = f.get("contentLength")
            try:
                filesize = int(filesize) if filesize else 0
            except Exception:
                filesize = 0
            video_streams.append({
                "itag": str(f.get("itag", res_str)),
                "resolution": res_str,
                "fps": f.get("fps", 30),
                "mime_type": "video/mp4",
                "extension": "mp4",
                "filesize": filesize,
                "filesize_formatted": format_size(filesize) if filesize else "HD Stream",
                "has_audio": True,
            })

    video_streams.sort(
        key=lambda x: int(re.search(r'(\d+)', x['resolution']).group(1)) if re.search(r'(\d+)', x['resolution']) else 0,
        reverse=True
    )

    approx_320 = int((320 * 1000 / 8) * duration) if duration else 0
    approx_192 = int((192 * 1000 / 8) * duration) if duration else 0
    approx_128 = int((128 * 1000 / 8) * duration) if duration else 0
    audio_streams = [
        {"itag": "bestaudio",     "abr": "320kbps", "mime_type": "audio/mp3", "extension": "mp3",
         "filesize": approx_320, "filesize_formatted": format_size(approx_320, is_approx=True) if duration else "320 kbps MP3"},
        {"itag": "bestaudio_192", "abr": "192kbps", "mime_type": "audio/mp3", "extension": "mp3",
         "filesize": approx_192, "filesize_formatted": format_size(approx_192, is_approx=True) if duration else "192 kbps MP3"},
        {"itag": "bestaudio_128", "abr": "128kbps", "mime_type": "audio/mp3", "extension": "mp3",
         "filesize": approx_128, "filesize_formatted": format_size(approx_128, is_approx=True) if duration else "128 kbps MP3"},
    ]

    return {
        "is_playlist": False,
        "title": title,
        "author": author,
        "length_seconds": duration,
        "length_formatted": format_duration(duration),
        "views": views,
        "thumbnail_url": thumbnail_url,
        "video_streams": video_streams,
        "audio_streams": audio_streams,
        "_source": "innertube",
    }


def invidious_get_info(video_id: str) -> Optional[dict]:
    """Fetch video info from Invidious API — last cookie-free fallback."""
    if not HAS_HTTPX:
        return None

    proxy_kwargs = {"proxy": PROXY_URL} if PROXY_URL else {}
    fields = "title,author,lengthSeconds,viewCount,videoThumbnails,adaptiveFormats,formatStreams"

    # Shuffle to avoid hammering one instance
    shuffled = INVIDIOUS_INSTANCES.copy()
    random.shuffle(shuffled)

    for instance in shuffled:
        try:
            url = f"{instance}/api/v1/videos/{video_id}?fields={fields}"
            resp = httpx.get(url, timeout=8, follow_redirects=True, **proxy_kwargs)
            if resp.status_code == 200:
                data = resp.json()
                if data.get("title"):
                    print(f"Invidious success: {instance}")
                    return {"_from_invidious": True, **data}
        except Exception as e:
            print(f"Invidious {instance} failed: {e}")
            continue
    return None


def invidious_info_to_response(data: dict, url: str) -> dict:
    """Convert Invidious API response to our standard format."""
    title = data.get("title", "Unknown Title")
    author = data.get("author", "Unknown")
    duration = data.get("lengthSeconds", 0)
    views = data.get("viewCount", 0)
    thumbnails = data.get("videoThumbnails", [])
    thumbnail = next((t["url"] for t in thumbnails if t.get("quality") in ["maxres", "high", "medium"]), "")
    if thumbnail and thumbnail.startswith("/"):
        thumbnail = f"https://i.ytimg.com{thumbnail}"
    seen_res, video_streams = set(), []
    for f in data.get("adaptiveFormats", []):
        height = f.get("resolution", "").replace("p", "")
        if not height:
            continue
        try:
            height = int(height)
        except Exception:
            continue
        if "video" not in f.get("type", ""):
            continue
        res_str = f"{height}p"
        if res_str not in seen_res:
            seen_res.add(res_str)
            filesize = int(f.get("clen") or 0)
            video_streams.append({
                "itag": f.get("itag", res_str), "resolution": res_str, "fps": f.get("fps", 30),
                "mime_type": "video/mp4", "extension": "mp4", "filesize": filesize,
                "filesize_formatted": format_size(filesize) if filesize else "HD Stream", "has_audio": True,
            })
    video_streams.sort(key=lambda x: int(re.search(r'(\d+)', x['resolution']).group(1) or 0), reverse=True)
    a320 = int((320*1000/8)*duration) if duration else 0
    a192 = int((192*1000/8)*duration) if duration else 0
    a128 = int((128*1000/8)*duration) if duration else 0
    return {
        "is_playlist": False, "title": title, "author": author,
        "length_seconds": duration, "length_formatted": format_duration(duration),
        "views": views, "thumbnail_url": thumbnail,
        "video_streams": video_streams,
        "audio_streams": [
            {"itag": "bestaudio",     "abr": "320kbps", "mime_type": "audio/mp3", "extension": "mp3", "filesize": a320, "filesize_formatted": format_size(a320, is_approx=True) if duration else "320 kbps MP3"},
            {"itag": "bestaudio_192", "abr": "192kbps", "mime_type": "audio/mp3", "extension": "mp3", "filesize": a192, "filesize_formatted": format_size(a192, is_approx=True) if duration else "192 kbps MP3"},
            {"itag": "bestaudio_128", "abr": "128kbps", "mime_type": "audio/mp3", "extension": "mp3", "filesize": a128, "filesize_formatted": format_size(a128, is_approx=True) if duration else "128 kbps MP3"},
        ],
        "_source": "invidious",
    }



@app.post("/api/info")
def get_video_info(req: VideoInfoRequest):
    """Extract metadata and available streams for video, audio, or playlist."""
    if not req.url:
        raise HTTPException(status_code=400, detail="Please provide a valid YouTube or YouTube Music URL.")

    url = clean_url(req.url)

    # ── Attempt 1: yt-dlp with multi-strategy retry ───────────────────────────
    if HAS_YTDLP:
        try:
            info = _ytdlp_extract_info(url)
            if info is not None:
                # Playlist or radio mix
                if info.get('_type') == 'playlist' or 'entries' in info:
                    entries = list(info.get('entries', []))
                    tracks = []
                    for e in entries[:50]:
                        if e:
                            track_id = e.get('id')
                            tracks.append({
                                "id": track_id,
                                "title": e.get('title', 'Unknown Track'),
                                "author": e.get('uploader') or e.get('channel', 'Unknown Artist'),
                                "duration_formatted": format_duration(e.get('duration', 0)),
                                "url": f"https://www.youtube.com/watch?v={track_id}",
                                "thumbnail_url": e.get('thumbnail') or (
                                    f"https://i.ytimg.com/vi/{track_id}/hqdefault.jpg" if track_id else None
                                )
                            })
                    return {
                        "is_playlist": True,
                        "title": info.get('title', 'YouTube Playlist'),
                        "author": info.get('uploader') or 'YouTube / YouTube Music',
                        "track_count": len(entries),
                        "tracks": tracks,
                        "playlist_url": url,
                    }

                # Single video
                title = info.get('title', 'Unknown Title')
                author = info.get('uploader') or info.get('channel', 'Unknown Channel')
                duration = info.get('duration', 0)
                views = info.get('view_count', 0)
                thumbnail = info.get('thumbnail', '')
                raw_formats = info.get('formats', [])

                seen_res = set()
                video_streams = []
                for f in reversed(raw_formats):
                    height = f.get('height')
                    vcodec = f.get('vcodec', 'none')
                    if height and vcodec != 'none':
                        res_str = f"{height}p"
                        if res_str not in seen_res:
                            seen_res.add(res_str)
                            filesize = f.get('filesize') or f.get('filesize_approx')
                            is_approx = False
                            if not filesize and duration:
                                tbr = f.get('tbr') or f.get('vbr') or 0
                                if tbr:
                                    filesize = int(((tbr + 192) * 1000 / 8) * duration)
                                    is_approx = True
                            video_streams.append({
                                "itag": f.get('format_id'),
                                "resolution": res_str,
                                "fps": f.get('fps') or 30,
                                "mime_type": "video/mp4",
                                "extension": "mp4",
                                "filesize": filesize or 0,
                                "filesize_formatted": format_size(filesize, is_approx=is_approx) if filesize else "HD Stream",
                                "has_audio": True,
                            })

                def parse_res(item):
                    match = re.search(r'(\d+)', item.get('resolution') or '')
                    return int(match.group(1)) if match else 0

                # If only 1 progressive stream was found (e.g. 360p), ensure standard HD options (1080p, 720p, 480p) are available
                available_res = {s['resolution'] for s in video_streams}
                if len(video_streams) <= 1 and duration:
                    standards = [
                        ("1080p", 3500, "1080p"),
                        ("720p", 1800, "720p"),
                        ("480p", 900, "480p"),
                        ("360p", 500, "360p"),
                    ]
                    for res_name, kbps, tag in standards:
                        if res_name not in available_res:
                            approx_bytes = int((kbps * 1000 / 8) * duration)
                            video_streams.append({
                                "itag": tag,
                                "resolution": res_name,
                                "fps": 30,
                                "mime_type": "video/mp4",
                                "extension": "mp4",
                                "filesize": approx_bytes,
                                "filesize_formatted": format_size(approx_bytes, is_approx=True),
                                "has_audio": True,
                            })

                video_streams.sort(key=parse_res, reverse=True)

                approx_audio = int((192 * 1000 / 8) * duration) if duration else 0
                audio_streams = [
                    {"itag": "bestaudio",     "abr": "320kbps", "mime_type": "audio/mp3", "extension": "mp3",
                     "filesize": int((320 * 1000 / 8) * duration) if duration else 0,
                     "filesize_formatted": format_size(int((320 * 1000 / 8) * duration), is_approx=True) if duration else "320 kbps MP3"},
                    {"itag": "bestaudio_192", "abr": "192kbps", "mime_type": "audio/mp3", "extension": "mp3",
                     "filesize": approx_audio,
                     "filesize_formatted": format_size(approx_audio, is_approx=True) if duration else "192 kbps MP3"},
                    {"itag": "bestaudio_128", "abr": "128kbps", "mime_type": "audio/mp3", "extension": "mp3",
                     "filesize": int((128 * 1000 / 8) * duration) if duration else 0,
                     "filesize_formatted": format_size(int((128 * 1000 / 8) * duration), is_approx=True) if duration else "128 kbps MP3"},
                ]

                return {
                    "is_playlist": False,
                    "title": title,
                    "author": author,
                    "length_seconds": duration,
                    "length_formatted": format_duration(duration),
                    "views": views,
                    "thumbnail_url": thumbnail,
                    "video_streams": video_streams,
                    "audio_streams": audio_streams,
                }
        except HTTPException:
            raise
        except Exception as e:
            print(f"[yt-dlp] All strategies exhausted: {str(e)[:200]}")
            # Fall through to cookie-free fallbacks

    # ── Fallback 1: YouTube Innertube API (Smart TV / Android VR / iOS clients) ─
    if HAS_HTTPX:
        video_id = extract_video_id(url)
        if video_id:
            print(f"[innertube] Trying for {video_id}...")
            innertube_data = innertube_get_info(video_id)
            if innertube_data:
                return innertube_to_response(innertube_data, video_id)

            # ── Fallback 2: Invidious public instances ────────────────────────
            print(f"[invidious] Trying for {video_id}...")
            inv_data = invidious_get_info(video_id)
            if inv_data:
                return invidious_info_to_response(inv_data, url)

    # ── All engines failed — give actionable tips ─────────────────────────────
    tips = []
    if not PROXY_URL:
        tips.append("Set PROXY_URL in Render → Environment Variables (residential proxy fixes this instantly)")
    if not PO_TOKEN:
        tips.append("Set PO_TOKEN from yt-dlp wiki (Proof of Origin token)")
    if not COOKIE_FILES:
        tips.append("Set YOUTUBE_COOKIES in Render → Environment Variables")

    tip_str = " | ".join(tips) if tips else "All bypass methods configured — YouTube may have updated bot detection"
    raise HTTPException(
        status_code=400,
        detail=f"All engines failed. YouTube is blocking this server's IP. Fix: {tip_str}"
    )




@app.get("/api/download")
def download_stream(
    url: str = Query(..., description="YouTube video URL"),
    itag: str = Query(..., description="Selected stream itag or format_id"),
    audio_only: bool = Query(False, description="Whether to download audio only")
):
    """Download single video (MP4) or audio (converted to genuine MP3) and stream."""
    url = clean_url(url)

    if HAS_YTDLP:
        try:
            tmp_dir = tempfile.mkdtemp(prefix="ytdl_")
            out_template = os.path.join(tmp_dir, "%(title)s.%(ext)s")
            has_ffmpeg = bool(shutil.which('ffmpeg'))

            # Build download format string
            def _build_fmt(ydl_opts_: dict):
                if audio_only:
                    ydl_opts_['format'] = 'bestaudio/best'
                    if has_ffmpeg:
                        ydl_opts_['postprocessors'] = [{
                            'key': 'FFmpegExtractAudio',
                            'preferredcodec': 'mp3',
                            'preferredquality': '192',
                        }]
                else:
                    if itag.isdigit():
                        fmt = f"{itag}+bestaudio/best/{itag}/best" if has_ffmpeg else f"{itag}/best"
                    elif itag in ["1080p", "720p", "480p", "360p", "240p", "144p"]:
                        height_val = itag.replace("p", "")
                        fmt = f"bestvideo[height<={height_val}]+bestaudio/best[height<={height_val}]/best" if has_ffmpeg else f"best[height<={height_val}]/best"
                    else:
                        fmt = "bestvideo+bestaudio/best" if has_ffmpeg else "best"
                    ydl_opts_['format'] = fmt

            # Multi-strategy download
            strategies_dl = []

            s1 = get_base_ydl_opts()
            s1['outtmpl'] = out_template
            _build_fmt(s1)
            strategies_dl.append(("cookies+optimal", s1))

            if COOKIE_FILES:
                s2 = get_base_ydl_opts()
                s2.pop('cookiefile', None)
                s2['outtmpl'] = out_template
                s2['extractor_args'] = {'youtube': {'player_client': ['tv_embedded', 'web_embedded']}}
                if PROXY_URL:
                    s2['proxy'] = PROXY_URL
                _build_fmt(s2)
                strategies_dl.append(("no-cookies+tv_embedded", s2))

            if PROXY_URL:
                s3 = get_base_ydl_opts()
                s3['proxy'] = PROXY_URL
                s3['outtmpl'] = out_template
                s3['extractor_args'] = {'youtube': {'player_client': ['web', 'tv_embedded', 'ios', 'android']}}
                _build_fmt(s3)
                strategies_dl.append(("proxy+all-clients", s3))

            info = None
            downloaded_file = None

            for strategy_name, ydl_opts in strategies_dl:
                try:
                    print(f"[download] Trying strategy: {strategy_name}")
                    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                        info = ydl.extract_info(url, download=True)
                        downloaded_file = ydl.prepare_filename(info)

                    if audio_only:
                        base_name = os.path.splitext(downloaded_file)[0]
                        mp3_path = base_name + ".mp3"
                        if os.path.exists(mp3_path):
                            downloaded_file = mp3_path

                    if not os.path.exists(downloaded_file):
                        files = os.listdir(tmp_dir)
                        downloaded_file = os.path.join(tmp_dir, files[0]) if files else None

                    if downloaded_file and os.path.exists(downloaded_file):
                        print(f"[download] Success with strategy: {strategy_name}")
                        break

                except Exception as e:
                    err_msg = str(e)
                    is_bot = (
                        "Sign in to confirm" in err_msg or "bot" in err_msg.lower() or
                        "cookies" in err_msg.lower()
                    )
                    print(f"[download] Strategy '{strategy_name}' failed (bot={is_bot}): {err_msg[:120]}")
                    if not is_bot:
                        raise HTTPException(status_code=400, detail=f"Download failed: {err_msg}")
                    continue

            if not downloaded_file or not os.path.exists(downloaded_file):
                raise HTTPException(status_code=500, detail="Downloaded file was not found on server.")

            safe_title = sanitize_filename(info.get('title', 'video') if info else 'video')
            ext = "mp3" if audio_only else (os.path.splitext(downloaded_file)[1].lstrip('.') or 'mp4')
            raw_filename = f"{(info.get('title', 'video') if info else 'video')}.{ext}"
            ascii_filename = f"{safe_title}.{ext}"
            utf8_encoded_filename = urllib.parse.quote(raw_filename)
            filesize = os.path.getsize(downloaded_file)

            def iterfile():
                try:
                    with open(downloaded_file, mode="rb") as f:
                        while chunk := f.read(1024 * 256):
                            yield chunk
                finally:
                    try:
                        shutil.rmtree(tmp_dir, ignore_errors=True)
                    except Exception:
                        pass

            headers = {
                "Content-Disposition": f'attachment; filename="{ascii_filename}"; filename*=UTF-8\'\'{utf8_encoded_filename}',
                "Content-Length": str(filesize),
                "Access-Control-Expose-Headers": "Content-Disposition, Content-Length",
            }

            mime_type = "audio/mpeg" if audio_only else f"video/{ext}"
            return StreamingResponse(iterfile(), headers=headers, media_type=mime_type)

        except HTTPException:
            raise
        except Exception as err:
            raise HTTPException(status_code=500, detail=f"Download error: {str(err)}")

    raise HTTPException(status_code=500, detail="Download engine not available.")


@app.get("/api/playlist-zip-sse")
def playlist_zip_sse(
    url: str = Query(..., description="YouTube playlist URL"),
    audio_only: bool = Query(True, description="Download as MP3 (True) or MP4 video (False)"),
    max_tracks: int = Query(10, description="Max number of tracks to download"),
):
    """
    Stream playlist progress via Server-Sent Events (SSE), download tracks,
    package into a single ZIP file, and return the download file_id when ready.
    """
    clean_target_url = clean_url(url)
    tracks_limit = min(max(1, max_tracks), 50)  # Safe cap between 1 and 50 tracks

    def event_stream():
        tmp_dir = tempfile.mkdtemp(prefix="pl_sse_")
        tracks_dir = os.path.join(tmp_dir, "tracks")
        os.makedirs(tracks_dir, exist_ok=True)

        try:
            yield f"data: {json.dumps({'type': 'init', 'status': 'Analyzing playlist tracks...'})}\n\n"

            # 1. Fetch playlist metadata
            ydl_opts_info = get_base_ydl_opts()
            ydl_opts_info.update({
                'skip_download': True,
                'extract_flat': 'in_playlist',
            })
            with yt_dlp.YoutubeDL(ydl_opts_info) as ydl:
                info = ydl.extract_info(clean_target_url, download=False)
                playlist_title = sanitize_filename(info.get('title') or 'Playlist')
                entries = list(info.get('entries', []))[:tracks_limit]
                total_tracks = len(entries)

            if not total_tracks:
                yield f"data: {json.dumps({'type': 'error', 'message': 'No tracks found in playlist.'})}\n\n"
                return

            yield f"data: {json.dumps({'type': 'start', 'total': total_tracks, 'playlist_title': playlist_title})}\n\n"

            # 2. Download and convert each track with live progress
            start_time = time.time()
            completed_count = 0

            for i, entry in enumerate(entries, start=1):
                track_id = entry.get('id') if isinstance(entry, dict) else None
                track_title = (entry.get('title') if isinstance(entry, dict) else None) or f"Track {i}"
                track_url = f"https://www.youtube.com/watch?v={track_id}" if track_id else clean_target_url

                # Estimate time remaining
                elapsed = time.time() - start_time
                if completed_count > 0:
                    avg_per_track = elapsed / completed_count
                    remaining_tracks = total_tracks - completed_count
                    eta_sec = max(1, int(avg_per_track * remaining_tracks))
                    eta_text = f"~{eta_sec}s left"
                else:
                    eta_text = "Calculating ETA..."

                percent = int(((i - 1) / total_tracks) * 90)

                yield f"data: {json.dumps({'type': 'progress', 'current': i, 'total': total_tracks, 'title': track_title, 'percent': percent, 'eta': eta_text})}\n\n"

                has_ffmpeg = bool(shutil.which('ffmpeg'))
                out_tmpl = os.path.join(tracks_dir, f"{i:02d} - %(title)s.%(ext)s")
                ydl_opts_dl = get_base_ydl_opts()
                ydl_opts_dl.update({
                    'outtmpl': out_tmpl,
                    'ignoreerrors': True,
                })
                if audio_only:
                    ydl_opts_dl['format'] = 'bestaudio/best'
                    if has_ffmpeg:
                        ydl_opts_dl['postprocessors'] = [{
                            'key': 'FFmpegExtractAudio',
                            'preferredcodec': 'mp3',
                            'preferredquality': '192',
                        }]
                else:
                    ydl_opts_dl['format'] = 'bestvideo[height<=720]+bestaudio/best[height<=720]/best' if has_ffmpeg else 'best[height<=720]/best'

                try:
                    with yt_dlp.YoutubeDL(ydl_opts_dl) as ydl_track:
                        ydl_track.download([track_url])
                    completed_count += 1
                except Exception as e:
                    print(f"Error on track {i}: {e}")

            # 3. Zip all downloaded files
            yield f"data: {json.dumps({'type': 'zipping', 'percent': 95, 'status': 'Packaging tracks into ZIP archive...'})}\n\n"

            downloaded_files = [f for f in os.listdir(tracks_dir) if os.path.isfile(os.path.join(tracks_dir, f))]
            if not downloaded_files:
                yield f"data: {json.dumps({'type': 'error', 'message': 'Could not download tracks from this playlist.'})}\n\n"
                return

            zip_filename = f"{playlist_title}.zip"
            zip_path = os.path.join(tmp_dir, zip_filename)

            with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
                for f in downloaded_files:
                    zf.write(os.path.join(tracks_dir, f), arcname=f)

            file_id = str(uuid.uuid4())
            zip_size = os.path.getsize(zip_path)

            # Store in memory for download
            ZIP_STORAGE[file_id] = {
                'path': zip_path,
                'tmp_dir': tmp_dir,
                'filename': zip_filename,
                'size': zip_size,
                'created_at': time.time(),
            }

            yield f"data: {json.dumps({'type': 'complete', 'percent': 100, 'file_id': file_id, 'filename': zip_filename, 'size_formatted': format_size(zip_size), 'total_tracks': len(downloaded_files)})}\n\n"

        except Exception as err:
            shutil.rmtree(tmp_dir, ignore_errors=True)
            yield f"data: {json.dumps({'type': 'error', 'message': str(err)})}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.get("/api/get-zip-file")
def get_zip_file(file_id: str = Query(...)):
    """Serve the generated ZIP file and clean up temp folder after transfer."""
    item = ZIP_STORAGE.pop(file_id, None)
    if not item or not os.path.exists(item['path']):
        raise HTTPException(status_code=404, detail="ZIP archive expired or not found.")

    zip_path = item['path']
    tmp_dir = item['tmp_dir']
    filename = item['filename']
    filesize = item['size']

    def iterzip():
        try:
            with open(zip_path, mode="rb") as f:
                while chunk := f.read(1024 * 256):
                    yield chunk
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    safe_zip_ascii = sanitize_filename(filename.replace('.zip', '')) + ".zip"
    utf8_zip_encoded = urllib.parse.quote(filename)

    headers = {
        "Content-Disposition": f'attachment; filename="{safe_zip_ascii}"; filename*=UTF-8\'\'{utf8_zip_encoded}',
        "Content-Length": str(filesize),
        "Access-Control-Expose-Headers": "Content-Disposition, Content-Length",
    }
    return StreamingResponse(iterzip(), headers=headers, media_type="application/zip")
