import os
import sys
import re
import json
import time
import uuid
import shutil
import tempfile
import zipfile
import threading
import queue
import urllib.parse
from typing import Optional, List, Dict, Any

from fastapi import FastAPI, HTTPException, Query, BackgroundTasks, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, FileResponse, JSONResponse
from pydantic import BaseModel

# Enable static-ffmpeg if available (useful in some containerized environments)
try:
    import static_ffmpeg
    static_ffmpeg.add_paths()
except Exception:
    pass

import yt_dlp

# ── App Initialization ────────────────────────────────────────────────────────
app = FastAPI(
    title="StreamCraft API",
    description="High-performance backend powered directly by yt-dlp for video info, MP3 conversion, and live SSE downloads",
    version="2.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)

# ── In-Memory Storages & Active Tasks ──────────────────────────────────────────
SINGLE_FILE_STORAGE: Dict[str, Dict[str, Any]] = {}
ZIP_STORAGE: Dict[str, Dict[str, Any]] = {}
ACTIVE_DOWNLOADS: Dict[str, threading.Event] = {}

# ── Configuration & Environment Helpers ───────────────────────────────────────
PO_TOKEN: Optional[str] = os.environ.get("PO_TOKEN") or None


def get_proxy_pool() -> List[str]:
    """Discover all proxies from PROXY_URL, PROXY_LIST, PROXY_URL_1..N, HTTPS_PROXY, HTTP_PROXY."""
    proxies = []
    
    # 1. Comma, semicolon, or newline separated lists in PROXY_URL or PROXY_LIST
    for key in ["PROXY_URL", "PROXY_LIST", "PROXIES", "HTTPS_PROXY", "HTTP_PROXY"]:
        val = os.environ.get(key)
        if val and val.strip():
            for item in re.split(r"[,;\n]+", val):
                p = item.strip()
                if p and p not in proxies:
                    proxies.append(p)

    # 2. Numbered environment variables: PROXY_URL_1, PROXY_URL_2, etc.
    for k, v in os.environ.items():
        if k.startswith("PROXY_URL_") and v.strip():
            p = v.strip()
            if p not in proxies:
                proxies.append(p)

    return proxies


def get_active_proxy() -> Optional[str]:
    """Pick a random proxy from the available pool for automatic load balancing/rotation."""
    pool = get_proxy_pool()
    if pool:
        import random
        return random.choice(pool)
    return None


def format_netscape_cookies(raw_text: str) -> str:
    """Auto-detect and format cookies from Netscape text, JSON, or Base64 into valid Netscape format."""
    raw = raw_text.strip()
    if not raw:
        return ""

    # 1. Decode base64 if detected
    if not raw.startswith("#") and not raw.startswith(".") and not raw.startswith("[") and len(raw) > 20:
        try:
            import base64
            decoded = base64.b64decode(raw).decode("utf-8", errors="ignore").strip()
            if decoded.startswith("#") or "youtube" in decoded or decoded.startswith("["):
                raw = decoded
        except Exception:
            pass

    # 2. Convert JSON cookie format (from Chrome extensions) to Netscape
    if raw.startswith("[") or raw.startswith("{"):
        try:
            data = json.loads(raw)
            if isinstance(data, dict):
                data = [data]
            lines = ["# Netscape HTTP Cookie File"]
            for c in data:
                dom = c.get("domain", ".youtube.com")
                sub = "TRUE" if dom.startswith(".") else "FALSE"
                path = c.get("path", "/")
                sec = "TRUE" if c.get("secure", False) else "FALSE"
                exp = str(int(c.get("expirationDate") or c.get("expiry") or (time.time() + 86400 * 365)))
                name = c.get("name", "")
                val = c.get("value", "")
                if name and val:
                    lines.append(f"{dom}\t{sub}\t{path}\t{sec}\t{exp}\t{name}\t{val}")
            if len(lines) > 1:
                return "\n".join(lines) + "\n"
        except Exception:
            pass

    # 3. Clean and normalize Netscape cookie text lines
    lines = ["# Netscape HTTP Cookie File"]
    for l in raw.splitlines():
        line_clean = l.strip()
        if not line_clean or line_clean.startswith("#"):
            continue
        parts = re.split(r"\t+|\s{2,}", line_clean)
        if len(parts) >= 7:
            lines.append("\t".join(parts[:7]))
        elif len(parts) == 6:
            lines.append(f"{parts[0]}\tTRUE\t{parts[1]}\tTRUE\t{parts[2]}\t{parts[3]}\t{parts[4]}")

    if len(lines) > 1:
        return "\n".join(lines) + "\n"
    return ""


def get_cookie_file_path() -> Optional[str]:
    """Find local cookie file or write environment variable YOUTUBE_COOKIES to a validated Netscape temp file."""
    # Check env variables: YOUTUBE_COOKIES, YOUTUBE_COOKIES_1..N, YT_COOKIES, COOKIES
    env_cookie_candidates = []
    for k, v in os.environ.items():
        if (k.startswith("YOUTUBE_COOKIE") or k.startswith("YT_COOKIE") or k == "COOKIES") and v.strip():
            env_cookie_candidates.append(v.strip())

    for raw_content in env_cookie_candidates:
        formatted = format_netscape_cookies(raw_content)
        if formatted and len(formatted.splitlines()) > 1:
            try:
                tf = tempfile.NamedTemporaryFile(delete=False, suffix="_ytdlp_env_cookie.txt", mode="w", encoding="utf-8")
                tf.write(formatted)
                tf.close()
                return tf.name
            except Exception as e:
                print(f"[Cookies] Failed to write temp cookie from env: {e}")

    # Check candidate local files
    candidate_paths = [
        "cookie.txt",
        "cookies.txt",
        "cookie_clean.txt",
        "test_cookie.txt",
        os.path.join(os.path.dirname(__file__), "..", "cookie.txt"),
        os.path.join(os.path.dirname(__file__), "..", "cookie_clean.txt"),
        os.path.join(os.path.dirname(__file__), "cookie.txt"),
    ]
    for cp in candidate_paths:
        if os.path.exists(cp) and os.path.getsize(cp) > 10:
            try:
                with open(cp, "r", encoding="utf-8", errors="ignore") as f:
                    file_content = f.read()
                cleaned_content = format_netscape_cookies(file_content)
                if cleaned_content:
                    tf = tempfile.NamedTemporaryFile(delete=False, suffix="_local_cookie.txt", mode="w", encoding="utf-8")
                    tf.write(cleaned_content)
                    tf.close()
                    return tf.name
            except Exception:
                return os.path.abspath(cp)

    return None


def get_base_ydl_opts(extra_opts: Optional[dict] = None) -> dict:
    """Build standard yt-dlp options dictionary with cookies, proxy, and robust extractor options."""
    cookie_file = get_cookie_file_path()
    active_proxy = get_active_proxy()

    opts: dict = {
        "quiet": True,
        "no_warnings": True,
        "nocheckcertificate": True,
        "ignoreerrors": False,
        "socket_timeout": 15,
        "http_chunk_size": 10485760,  # 10MB chunks for fast parallel streaming
        "retries": 3,
        "fragment_retries": 3,
    }

    if PO_TOKEN:
        opts["extractor_args"] = {"youtube": {"po_token": [f"web+{PO_TOKEN}"]}}

    if cookie_file and os.path.exists(cookie_file):
        opts["cookiefile"] = cookie_file

    if active_proxy:
        opts["proxy"] = active_proxy

    if extra_opts:
        opts.update(extra_opts)

    return opts


# ── String & Format Formatting Helpers ─────────────────────────────────────────
def format_size(bytes_size: Optional[int], is_approx: bool = False) -> str:
    """Format bytes into human-readable string (MB, GB, etc.)."""
    if not bytes_size or bytes_size <= 0:
        return "Unknown size"
    size = float(bytes_size)
    prefix = "~" if is_approx else ""
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if size < 1024.0:
            return f"{prefix}{size:.1f} {unit}"
        size /= 1024.0
    return f"{prefix}{size:.1f} PB"


def format_duration(seconds: Optional[float]) -> str:
    """Format duration into HH:MM:SS or MM:SS."""
    if not seconds:
        return "0:00"
    total_secs = int(seconds)
    mins, secs = divmod(total_secs, 60)
    hours, mins = divmod(mins, 60)
    if hours > 0:
        return f"{hours:02d}:{mins:02d}:{secs:02d}"
    return f"{mins:02d}:{secs:02d}"


def format_views(view_count: Optional[int]) -> str:
    """Format view counts into readable K/M/B."""
    if not view_count or view_count < 0:
        return "0 views"
    if view_count >= 1_000_000_000:
        return f"{view_count / 1_000_000_000:.1f}B views"
    if view_count >= 1_000_000:
        return f"{view_count / 1_000_000:.1f}M views"
    if view_count >= 1_000:
        return f"{view_count / 1_000:.1f}K views"
    return f"{view_count:,} views"


def sanitize_filename(name: str) -> str:
    """Sanitize string to be filesystem safe and ASCII compatible."""
    clean = re.sub(r'[\\/*?:"<>|]', "", name)
    clean = clean.encode("ascii", "ignore").decode("ascii").strip()
    return clean or "media"


def clean_url(url: str) -> str:
    """Normalize user input, handling search queries, shorts, music URLs, and playlist parameters."""
    url = url.strip()
    if not url:
        return url

    # Search keyword support
    is_url = (
        url.startswith("http://") or
        url.startswith("https://") or
        "youtube.com" in url or
        "youtu.be" in url
    )
    if not is_url:
        if "." not in url or "/" not in url:
            return f"ytsearch1:{url}"

    url = re.sub(r"\s+", "", url)
    if not url.startswith("http://") and not url.startswith("https://"):
        url = "https://" + url

    # Map music.youtube.com to standard www.youtube.com
    url = url.replace("music.youtube.com", "www.youtube.com")

    # Transform shorts to watch
    if "/shorts/" in url:
        url = url.replace("/shorts/", "/watch?v=")

    # Transform youtu.be/ID to watch?v=ID
    if "youtu.be/" in url:
        parts = url.split("youtu.be/")[1].split("?")[0]
        url = f"https://www.youtube.com/watch?v={parts}"

    # Strip radio mix params from single video URLs
    if "watch?v=" in url and "&list=RD" in url:
        url = url.split("&list=RD")[0]

    return url.rstrip("&?")


# ── Schemas ───────────────────────────────────────────────────────────────────
class VideoInfoRequest(BaseModel):
    url: str


# ── Cleanup helper for old stored files ───────────────────────────────────────
def cleanup_old_files():
    """Remove stored temporary files older than 1 hour."""
    now = time.time()
    for sid, info in list(SINGLE_FILE_STORAGE.items()):
        if now - info.get("created_at", now) > 3600:
            try:
                if os.path.exists(info["filepath"]):
                    os.remove(info["filepath"])
            except Exception:
                pass
            SINGLE_FILE_STORAGE.pop(sid, None)

    for zid, info in list(ZIP_STORAGE.items()):
        if now - info.get("created_at", now) > 3600:
            try:
                if os.path.exists(info["filepath"]):
                    os.remove(info["filepath"])
            except Exception:
                pass
            ZIP_STORAGE.pop(zid, None)


# ── Endpoint: Health Check ───────────────────────────────────────────────────
@app.get("/api/health")
async def health_check():
    cookie_file = get_cookie_file_path()
    proxy_pool = get_proxy_pool()
    return {
        "status": "ok",
        "service": "StreamCraft Backend",
        "yt_dlp_version": getattr(yt_dlp.version, "__version__", "unknown"),
        "cookies_configured": bool(cookie_file),
        "proxy_configured": len(proxy_pool) > 0,
        "proxies_count": len(proxy_pool),
        "po_token_configured": bool(PO_TOKEN),
    }


# ── Endpoint: Video / Playlist Info ──────────────────────────────────────────
@app.post("/api/info")
async def get_info(request: VideoInfoRequest):
    cleanup_old_files()
    raw_url = clean_url(request.url)
    if not raw_url:
        raise HTTPException(status_code=400, detail="Please provide a valid YouTube URL or search query.")

    opts = get_base_ydl_opts({
        "extract_flat": "in_playlist",
        "skip_download": True,
    })

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(raw_url, download=False)
    except yt_dlp.utils.DownloadError as de:
        err_msg = str(de)
        if "Sign in to confirm" in err_msg or "bot" in err_msg.lower():
            raise HTTPException(
                status_code=403,
                detail="YouTube requested authentication. Please configure YOUTUBE_COOKIES or PROXY_URL in your deployment."
            )
        raise HTTPException(status_code=400, detail=f"yt-dlp extraction error: {err_msg}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch video details: {str(e)}")

    if not info:
        raise HTTPException(status_code=404, detail="No video or playlist found.")

    # Handle Search Queries & Single Video Results
    if info.get("_type") == "playlist" or "entries" in info:
        entries = [e for e in (info.get("entries") or []) if e]

        # If it was a search query or a playlist with 1 track that needs full format inspection
        if (raw_url.startswith("ytsearch") or "search" in str(info.get("extractor", ""))) and len(entries) >= 1:
            first_entry = entries[0]
            first_url = first_entry.get("url") or f"https://www.youtube.com/watch?v={first_entry.get('id')}"
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(first_url, download=False)
        elif info.get("_type") == "playlist" or len(entries) > 1:
            tracks = []
            for e in entries:
                v_id = e.get("id")
                if not v_id:
                    continue
                dur = e.get("duration")
                author_name = e.get("uploader") or e.get("channel") or info.get("uploader") or "Unknown Artist"
                thumb = e.get("thumbnail") or f"https://i.ytimg.com/vi/{v_id}/hqdefault.jpg"
                dur_fmt = format_duration(dur)
                tracks.append({
                    "id": v_id,
                    "title": e.get("title", "Untitled Track"),
                    "uploader": author_name,
                    "author": author_name,
                    "duration": dur,
                    "duration_formatted": dur_fmt,
                    "length_formatted": dur_fmt,
                    "thumbnail": thumb,
                    "thumbnail_url": thumb,
                    "url": f"https://www.youtube.com/watch?v={v_id}",
                })

            author_str = info.get("uploader") or info.get("channel") or "Unknown Uploader"
            return {
                "is_playlist": True,
                "id": info.get("id") or "playlist",
                "title": info.get("title") or "YouTube Playlist",
                "uploader": author_str,
                "author": author_str,
                "track_count": len(tracks),
                "total_tracks": len(tracks),
                "playlist_url": raw_url,
                "url": raw_url,
                "tracks": tracks,
            }

    # Handle Single Video response
    video_id = info.get("id") or "video"
    title = info.get("title") or "YouTube Video"
    uploader = info.get("uploader") or info.get("channel") or "Unknown"
    duration = info.get("duration") or 0
    dur_formatted = format_duration(duration)
    views = info.get("view_count") or 0
    thumbnail = info.get("thumbnail") or f"https://i.ytimg.com/vi/{video_id}/maxresdefault.jpg"
    description = info.get("description", "")

    # Parse and categorize formats
    formats = info.get("formats", [])
    seen_resolutions = set()
    video_streams = []

    # Priority target resolutions
    res_priority = [2160, 1440, 1080, 720, 480, 360, 240, 144]

    for target_h in res_priority:
        matching = [
            f for f in formats
            if f.get("vcodec") != "none" and (f.get("height") == target_h or (target_h == 2160 and (f.get("height") or 0) >= 2160))
        ]
        if not matching:
            continue

        matching.sort(key=lambda x: (x.get("filesize") or x.get("filesize_approx") or (x.get("tbr") or 0)), reverse=True)
        best_f = matching[0]

        res_label = f"{target_h}p"
        if target_h >= 2160:
            quality_desc = f"{res_label} (4K Ultra HD)"
        elif target_h >= 1440:
            quality_desc = f"{res_label} (2K Quad HD)"
        elif target_h >= 1080:
            quality_desc = f"{res_label} (Full HD)"
        elif target_h >= 720:
            quality_desc = f"{res_label} (HD)"
        else:
            quality_desc = f"{res_label} (SD)"

        if res_label not in seen_resolutions:
            seen_resolutions.add(res_label)
            f_size = best_f.get("filesize") or best_f.get("filesize_approx")
            is_approx = False
            if not f_size and duration and best_f.get("tbr"):
                f_size = int((best_f["tbr"] * 1000 / 8) * duration)
                is_approx = True

            fps_val = best_f.get("fps") or 30

            video_streams.append({
                "itag": str(best_f.get("format_id")),
                "resolution": res_label,
                "label": quality_desc,
                "quality": res_label,
                "extension": "mp4",
                "filesize": f_size,
                "filesize_formatted": format_size(f_size, is_approx=is_approx),
                "has_audio": best_f.get("acodec") != "none",
                "is_approx_size": is_approx,
                "fps": fps_val,
                "direct_url": best_f.get("url") if best_f.get("acodec") != "none" and best_f.get("ext") == "mp4" else None,
            })

    # High quality MP3 audio options with full abr + bitrate aliases
    audio_bitrates = [
        (320, "320 kbps (Ultra HD MP3)"),
        (256, "256 kbps (High Quality MP3)"),
        (192, "192 kbps (Standard Quality MP3)"),
        (128, "128 kbps (Compact MP3)"),
    ]

    audio_streams = []
    for kbps, label in audio_bitrates:
        approx_bytes = int((kbps * 1000 / 8) * duration) if duration else None
        audio_streams.append({
            "itag": f"{kbps}k",
            "abr": f"{kbps}kbps",
            "bitrate": f"{kbps} kbps",
            "label": label,
            "extension": "mp3",
            "filesize": approx_bytes,
            "filesize_formatted": format_size(approx_bytes, is_approx=True),
            "is_approx_size": True,
        })

    return {
        "is_playlist": False,
        "id": video_id,
        "title": title,
        "author": uploader,
        "uploader": uploader,
        "duration": duration,
        "duration_formatted": dur_formatted,
        "length_formatted": dur_formatted,
        "views": views,
        "views_formatted": format_views(views),
        "thumbnail": thumbnail,
        "thumbnail_url": thumbnail,
        "description": description[:500] if description else "",
        "video_streams": video_streams,
        "audio_streams": audio_streams,
    }


# ── Endpoint: SSE Single Video / Audio Download ───────────────────────────────
@app.get("/api/download-single-sse")
async def download_single_sse(
    url: str = Query(...),
    itag: str = Query(...),
    audio_only: bool = Query(False),
    download_id: Optional[str] = Query(None)
):
    """Real-time SSE download worker using yt-dlp with live progress hook."""
    cleanup_old_files()
    raw_url = clean_url(url)
    current_download_id = download_id or f"dl_{uuid.uuid4().hex[:8]}"
    cancel_event = threading.Event()
    ACTIVE_DOWNLOADS[current_download_id] = cancel_event

    msg_queue: queue.Queue = queue.Queue()
    # Put immediate initial progress event so the client un-hangs immediately
    msg_queue.put({
        "type": "progress",
        "percent": 8,
        "receivedMB": "0.1",
        "speed": "Starting...",
        "eta": "Few seconds",
        "status": "Connecting to YouTube stream...",
    })

    def run_download():
        temp_dir = tempfile.mkdtemp(prefix="streamcraft_single_")
        try:
            def progress_hook(d):
                if cancel_event.is_set():
                    raise yt_dlp.utils.DownloadCancelled("Download cancelled by user.")

                status = d.get("status")
                if status == "downloading":
                    downloaded = d.get("downloaded_bytes", 0)
                    total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                    speed = d.get("speed")
                    eta = d.get("eta")

                    percent = 10.0
                    if total > 0:
                        percent = min(98.0, max(10.0, (downloaded / total) * 100))

                    speed_str = format_size(int(speed)) + "/s" if speed else "Streaming..."
                    eta_str = f"{int(eta)}s" if eta is not None else "Few seconds"

                    msg_queue.put({
                        "type": "progress",
                        "percent": round(percent, 1),
                        "receivedMB": f"{downloaded / (1024 * 1024):.1f}",
                        "totalMB": f"{total / (1024 * 1024):.1f}" if total else "",
                        "speed": speed_str,
                        "eta": eta_str,
                        "status": f"Downloading stream ({round(percent, 1)}%)...",
                    })

                elif status == "finished":
                    msg_queue.put({
                        "type": "converting",
                        "percent": 95,
                        "speed": "Processing",
                        "eta": "Few seconds",
                        "status": "Merging / Converting media into final format...",
                    })

            # Base options
            ydl_opts = get_base_ydl_opts({
                "outtmpl": os.path.join(temp_dir, "%(title).200B.%(ext)s"),
                "progress_hooks": [progress_hook],
                "noplaylist": True,
            })

            if audio_only:
                bitrate_match = re.search(r"(\d+)k?", itag)
                desired_bitrate = bitrate_match.group(1) if bitrate_match else "192"

                ydl_opts.update({
                    "format": "bestaudio/best",
                    "postprocessors": [{
                        "key": "FFmpegExtractAudio",
                        "preferredcodec": "mp3",
                        "preferredquality": desired_bitrate,
                    }],
                })
            else:
                if itag and itag.isdigit():
                    ydl_opts["format"] = f"{itag}+bestaudio/bestvideo+bestaudio/best"
                else:
                    ydl_opts["format"] = "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best"

                ydl_opts["merge_output_format"] = "mp4"

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info_dict = ydl.extract_info(raw_url, download=True)
                title = info_dict.get("title", "media") if info_dict else "media"

            downloaded_files = [
                os.path.join(temp_dir, f) for f in os.listdir(temp_dir)
                if not f.endswith(".part") and not f.endswith(".ytdl")
            ]

            if not downloaded_files:
                raise RuntimeError("Media file was not created by yt-dlp.")

            final_filepath = downloaded_files[0]
            ext = os.path.splitext(final_filepath)[1].lstrip(".").lower() or ("mp3" if audio_only else "mp4")
            safe_title = sanitize_filename(title)
            final_filename = f"{safe_title}.{ext}"

            file_id = str(uuid.uuid4())
            file_size = os.path.getsize(final_filepath)

            SINGLE_FILE_STORAGE[file_id] = {
                "filepath": final_filepath,
                "filename": final_filename,
                "mimetype": "audio/mpeg" if ext == "mp3" else "video/mp4",
                "created_at": time.time(),
                "temp_dir": temp_dir,
            }

            msg_queue.put({
                "type": "complete",
                "file_id": file_id,
                "filename": final_filename,
                "size_formatted": format_size(file_size),
            })

        except yt_dlp.utils.DownloadCancelled:
            msg_queue.put({"type": "error", "message": "Download was cancelled by user."})
            shutil.rmtree(temp_dir, ignore_errors=True)
        except Exception as e:
            print(f"[Download Error] {e}")
            msg_queue.put({"type": "error", "message": f"Download error: {str(e)}"})
            shutil.rmtree(temp_dir, ignore_errors=True)
        finally:
            ACTIVE_DOWNLOADS.pop(current_download_id, None)

    t = threading.Thread(target=run_download, daemon=True)
    t.start()

    async def event_generator():
        while True:
            try:
                msg = msg_queue.get(timeout=1.0)
                yield f"data: {json.dumps(msg)}\n\n"
                if msg.get("type") in ("complete", "error"):
                    break
            except queue.Empty:
                yield ": keepalive\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        }
    )


# ── Endpoint: Download Stored Single File ─────────────────────────────────────
@app.get("/api/get-single-file")
async def get_single_file(file_id: str = Query(...)):
    info = SINGLE_FILE_STORAGE.get(file_id)
    if not info or not os.path.exists(info["filepath"]):
        raise HTTPException(status_code=404, detail="Requested file was not found or has expired.")

    return FileResponse(
        path=info["filepath"],
        filename=info["filename"],
        media_type=info.get("mimetype", "application/octet-stream"),
    )


# ── Endpoint: Direct Single Download (Vercel 502/504 Fallback) ────────────────
@app.get("/api/download-direct")
async def download_direct(
    url: str = Query(...),
    itag: str = Query(...),
    audio_only: bool = Query(False)
):
    """Direct one-step download endpoint without SSE, ideal for edge/Vercel proxies and direct browser downloads."""
    cleanup_old_files()
    raw_url = clean_url(url)
    temp_dir = tempfile.mkdtemp(prefix="streamcraft_direct_")

    ydl_opts = get_base_ydl_opts({
        "outtmpl": os.path.join(temp_dir, "%(title).200B.%(ext)s"),
        "noplaylist": True,
    })

    if audio_only:
        bitrate_match = re.search(r"(\d+)k?", itag)
        desired_bitrate = bitrate_match.group(1) if bitrate_match else "192"
        ydl_opts.update({
            "format": "bestaudio/best",
            "postprocessors": [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": desired_bitrate,
            }],
        })
    else:
        if itag and itag.isdigit():
            ydl_opts["format"] = f"{itag}+bestaudio/bestvideo+bestaudio/best"
        else:
            ydl_opts["format"] = "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best"
        ydl_opts["merge_output_format"] = "mp4"

    try:
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info_dict = ydl.extract_info(raw_url, download=True)
                title = info_dict.get("title", "media") if info_dict else "media"
        except Exception as pe:
            if ydl_opts.get("proxy"):
                print(f"[Proxy Failed] Retrying directly without proxy: {pe}")
                ydl_opts.pop("proxy", None)
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info_dict = ydl.extract_info(raw_url, download=True)
                    title = info_dict.get("title", "media") if info_dict else "media"
            else:
                raise pe

        downloaded_files = [
            os.path.join(temp_dir, f) for f in os.listdir(temp_dir)
            if not f.endswith(".part") and not f.endswith(".ytdl")
        ]
        if not downloaded_files:
            raise RuntimeError("Media file was not generated.")

        final_filepath = downloaded_files[0]
        ext = os.path.splitext(final_filepath)[1].lstrip(".").lower() or ("mp3" if audio_only else "mp4")
        safe_title = sanitize_filename(title)
        final_filename = f"{safe_title}.{ext}"

        return FileResponse(
            path=final_filepath,
            filename=final_filename,
            media_type="audio/mpeg" if ext == "mp3" else "video/mp4",
        )
    except Exception as e:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise HTTPException(status_code=500, detail=f"Direct download failed: {str(e)}")


# ── Endpoint: Cancel Download ─────────────────────────────────────────────────
@app.post("/api/cancel-download")
async def cancel_download(download_id: str = Query(...)):
    cancel_event = ACTIVE_DOWNLOADS.get(download_id)
    if cancel_event:
        cancel_event.set()
        return {"status": "cancelled", "download_id": download_id}
    return {"status": "not_found_or_finished", "download_id": download_id}


# ── Endpoint: SSE Playlist ZIP Download ───────────────────────────────────────
@app.get("/api/playlist-zip-sse")
async def playlist_zip_sse(
    url: str = Query(...),
    audio_only: bool = Query(True),
    max_tracks: int = Query(50)
):
    """Download an entire playlist track by track and stream progress, packaging into a ZIP file."""
    cleanup_old_files()
    raw_url = clean_url(url)
    msg_queue: queue.Queue = queue.Queue()

    def run_playlist_task():
        temp_dir = tempfile.mkdtemp(prefix="streamcraft_playlist_")
        try:
            flat_opts = get_base_ydl_opts({
                "extract_flat": True,
                "skip_download": True,
            })
            with yt_dlp.YoutubeDL(flat_opts) as ydl:
                info = ydl.extract_info(raw_url, download=False)

            if not info:
                raise RuntimeError("Failed to fetch playlist entries.")

            playlist_title = sanitize_filename(info.get("title") or "YouTube_Playlist")
            entries = list(info.get("entries") or [])
            if not entries and "url" in info:
                entries = [info]

            total_tracks = min(len(entries), max_tracks)
            if total_tracks == 0:
                raise RuntimeError("Playlist contains 0 tracks.")

            msg_queue.put({
                "type": "start",
                "total": total_tracks,
                "title": playlist_title,
            })

            track_files = []
            for i, entry in enumerate(entries[:total_tracks]):
                if not entry:
                    continue
                t_id = entry.get("id")
                t_title = entry.get("title") or f"Track {i+1}"
                t_url = f"https://www.youtube.com/watch?v={t_id}" if t_id else entry.get("url")
                if not t_url:
                    continue

                msg_queue.put({
                    "type": "progress",
                    "percent": round(((i) / total_tracks) * 85),
                    "current": i + 1,
                    "total": total_tracks,
                    "title": t_title,
                    "eta": f"Track {i+1} of {total_tracks}",
                })

                track_dir = tempfile.mkdtemp(prefix=f"track_{i}_", dir=temp_dir)
                t_opts = get_base_ydl_opts({
                    "outtmpl": os.path.join(track_dir, f"{i+1:02d} - %(title).150B.%(ext)s"),
                    "noplaylist": True,
                })

                if audio_only:
                    t_opts.update({
                        "format": "bestaudio/best",
                        "postprocessors": [{
                            "key": "FFmpegExtractAudio",
                            "preferredcodec": "mp3",
                            "preferredquality": "256",
                        }],
                    })
                else:
                    t_opts.update({
                        "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
                        "merge_output_format": "mp4",
                    })

                try:
                    with yt_dlp.YoutubeDL(t_opts) as ydl:
                        ydl.extract_info(t_url, download=True)

                    downloaded = [
                        os.path.join(track_dir, f) for f in os.listdir(track_dir)
                        if not f.endswith(".part") and not f.endswith(".ytdl")
                    ]
                    if downloaded:
                        track_files.append(downloaded[0])
                except Exception as te:
                    print(f"[Playlist Track {i+1}] Failed: {te}")
                    continue

            if not track_files:
                raise RuntimeError("Could not download any tracks from the playlist.")

            msg_queue.put({
                "type": "zipping",
                "percent": 90,
                "title": "Packaging ZIP Archive...",
                "eta": "Compressing...",
            })

            zip_filename = f"{playlist_title}.zip"
            zip_path = os.path.join(temp_dir, zip_filename)

            with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zipf:
                for fpath in track_files:
                    zipf.write(fpath, arcname=os.path.basename(fpath))

            file_id = str(uuid.uuid4())
            zip_size = os.path.getsize(zip_path)

            ZIP_STORAGE[file_id] = {
                "filepath": zip_path,
                "filename": zip_filename,
                "created_at": time.time(),
                "temp_dir": temp_dir,
            }

            msg_queue.put({
                "type": "complete",
                "file_id": file_id,
                "filename": zip_filename,
                "total_tracks": len(track_files),
                "size_formatted": format_size(zip_size),
            })

        except Exception as e:
            msg_queue.put({"type": "error", "message": str(e)})
            shutil.rmtree(temp_dir, ignore_errors=True)

    t = threading.Thread(target=run_playlist_task, daemon=True)
    t.start()

    async def event_generator():
        while True:
            try:
                msg = msg_queue.get(timeout=1.0)
                yield f"data: {json.dumps(msg)}\n\n"
                if msg.get("type") in ("complete", "error"):
                    break
            except queue.Empty:
                yield ": keepalive\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        }
    )


# ── Endpoint: Download Ready Playlist ZIP File ────────────────────────────────
@app.get("/api/get-zip-file")
async def get_zip_file(file_id: str = Query(...)):
    info = ZIP_STORAGE.get(file_id)
    if not info or not os.path.exists(info["filepath"]):
        raise HTTPException(status_code=404, detail="ZIP archive was not found or has expired.")

    return FileResponse(
        path=info["filepath"],
        filename=info["filename"],
        media_type="application/zip",
    )


# ── Endpoint: Direct Stream Proxy Pipe ─────────────────────────────────────────
@app.get("/api/proxy-pipe")
async def proxy_pipe(
    stream_url: str = Query(...),
    title: str = Query("media"),
    ext: str = Query("mp4")
):
    """Pipe direct CDN stream chunks directly to browser download."""
    import httpx

    safe_name = sanitize_filename(title)
    filename = f"{safe_name}.{ext}"
    safe_header_fn = urllib.parse.quote(filename)

    async def stream_cdn():
        async with httpx.AsyncClient(follow_redirects=True, timeout=60.0) as client:
            async with client.stream("GET", stream_url) as resp:
                async for chunk in resp.aiter_bytes(chunk_size=65536):
                    yield chunk

    media_type = "video/mp4" if ext == "mp4" else "audio/mpeg"
    return StreamingResponse(
        stream_cdn(),
        media_type=media_type,
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{safe_header_fn}",
        }
    )
