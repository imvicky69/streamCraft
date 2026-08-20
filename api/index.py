import os
import sys
import re
import json
import time
import uuid
import tempfile
import zipfile
import shutil
import urllib.parse
from typing import Optional, List, Dict
from fastapi import FastAPI, HTTPException, Query, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, FileResponse
from pydantic import BaseModel

# Enable static-ffmpeg if installed
try:
    import static_ffmpeg
    static_ffmpeg.add_paths()
except Exception:
    pass

# Try yt-dlp first
try:
    import yt_dlp
    HAS_YTDLP = True
except ImportError:
    HAS_YTDLP = False

app = FastAPI(
    title="YouTube & YouTube Music Downloader API",
    description="FastAPI backend for video info, MP3 conversion, and live SSE playlist ZIP downloads",
    version="1.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
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


def get_base_ydl_opts() -> dict:
    """Base yt-dlp options with iOS, Android, and mweb player clients to bypass cloud datacenter bot checks."""
    opts = {
        'quiet': True,
        'no_warnings': True,
        'socket_timeout': 15,
        'retries': 3,
        'extractor_args': {
            'youtube': {
                'player_client': ['ios', 'android', 'mweb', 'web_creator', 'tv_embedded'],
                'player_skip': ['webpage', 'configs'],
            }
        },
        'http_headers': {
            'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1',
            'Accept-Language': 'en-US,en;q=0.9',
        }
    }

    # Support optional cookies from environment variable for Vercel/cloud
    cookies_env = os.environ.get('YOUTUBE_COOKIES') or os.environ.get('YT_COOKIES')
    if cookies_env:
        try:
            cookie_file = tempfile.NamedTemporaryFile(delete=False, suffix='.txt', mode='w', encoding='utf-8')
            cookie_file.write(cookies_env)
            cookie_file.close()
            opts['cookiefile'] = cookie_file.name
        except Exception:
            pass

    return opts


@app.get("/api/health")
def health():
    return {
        "status": "ok",
        "engine": "yt-dlp" if HAS_YTDLP else "none",
        "message": "Downloader API is operational"
    }


@app.post("/api/info")
def get_video_info(req: VideoInfoRequest):
    """Extract metadata and available streams for video, audio, or playlist."""
    if not req.url:
        raise HTTPException(status_code=400, detail="Please provide a valid YouTube or YouTube Music URL.")

    url = clean_url(req.url)

    if HAS_YTDLP:
        try:
            ydl_opts = get_base_ydl_opts()
            ydl_opts.update({
                'skip_download': True,
                'extract_flat': 'in_playlist',
            })
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
                
                # Check if this is a playlist or radio mix
                if info.get('_type') == 'playlist' or 'entries' in info:
                    entries = list(info.get('entries', []))
                    tracks = []
                    for e in entries[:50]:  # Cap preview at 50 tracks
                        if e:
                            track_id = e.get('id')
                            tracks.append({
                                "id": track_id,
                                "title": e.get('title', 'Unknown Track'),
                                "author": e.get('uploader') or e.get('channel', 'Unknown Artist'),
                                "duration_formatted": format_duration(e.get('duration', 0)),
                                "url": f"https://www.youtube.com/watch?v={track_id}",
                                "thumbnail_url": e.get('thumbnail') or (f"https://i.ytimg.com/vi/{track_id}/hqdefault.jpg" if track_id else None)
                            })
                    
                    return {
                        "is_playlist": True,
                        "title": info.get('title', 'YouTube Playlist'),
                        "author": info.get('uploader') or 'YouTube / YouTube Music',
                        "track_count": len(entries),
                        "tracks": tracks,
                        "playlist_url": url,
                    }

                # Single video extraction
                title = info.get('title', 'Unknown Title')
                author = info.get('uploader') or info.get('channel', 'Unknown Channel')
                duration = info.get('duration', 0)
                views = info.get('view_count', 0)
                thumbnail = info.get('thumbnail', '')
                
                raw_formats = info.get('formats', [])
                video_streams = []
                audio_streams = []

                # Group best video formats by resolution with accurate calculated sizes
                seen_res = set()
                for f in reversed(raw_formats):
                    height = f.get('height')
                    vcodec = f.get('vcodec', 'none')
                    
                    if height and vcodec != 'none':
                        res_str = f"{height}p"
                        if res_str not in seen_res:
                            seen_res.add(res_str)
                            filesize = f.get('filesize') or f.get('filesize_approx')
                            is_approx = False
                            
                            # If filesize is not directly provided, calculate from bitrate & duration
                            if not filesize and duration:
                                tbr = f.get('tbr') or f.get('vbr') or 0
                                audio_bitrate = 192  # standard audio
                                if tbr:
                                    filesize = int(((tbr + audio_bitrate) * 1000 / 8) * duration)
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

                # Sort video streams by resolution
                def parse_res(item):
                    match = re.search(r'(\d+)', item.get('resolution') or '')
                    return int(match.group(1)) if match else 0

                video_streams.sort(key=parse_res, reverse=True)

                # Audio formats (Always MP3 with accurate size)
                approx_audio_size = int((192 * 1000 / 8) * duration) if duration else 0
                audio_streams = [
                    {
                        "itag": "bestaudio",
                        "abr": "320kbps",
                        "mime_type": "audio/mp3",
                        "extension": "mp3",
                        "filesize": int((320 * 1000 / 8) * duration) if duration else 0,
                        "filesize_formatted": format_size(int((320 * 1000 / 8) * duration), is_approx=True) if duration else "320 kbps MP3",
                    },
                    {
                        "itag": "bestaudio_192",
                        "abr": "192kbps",
                        "mime_type": "audio/mp3",
                        "extension": "mp3",
                        "filesize": approx_audio_size,
                        "filesize_formatted": format_size(approx_audio_size, is_approx=True) if duration else "192 kbps MP3",
                    },
                    {
                        "itag": "bestaudio_128",
                        "abr": "128kbps",
                        "mime_type": "audio/mp3",
                        "extension": "mp3",
                        "filesize": int((128 * 1000 / 8) * duration) if duration else 0,
                        "filesize_formatted": format_size(int((128 * 1000 / 8) * duration), is_approx=True) if duration else "128 kbps MP3",
                    }
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
            raise HTTPException(status_code=400, detail=f"Failed to fetch details: {str(e)}")

    raise HTTPException(status_code=500, detail="Download engine not available.")


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
            ydl_opts = get_base_ydl_opts()
            ydl_opts.update({
                'outtmpl': out_template,
            })

            if audio_only:
                ydl_opts['format'] = 'bestaudio/best'
                if has_ffmpeg:
                    ydl_opts['postprocessors'] = [{
                        'key': 'FFmpegExtractAudio',
                        'preferredcodec': 'mp3',
                        'preferredquality': '192',
                    }]
            else:
                if has_ffmpeg:
                    fmt = f"{itag}+bestaudio/best" if itag.isdigit() else "bestvideo+bestaudio/best/best"
                else:
                    fmt = f"{itag}/bestvideo+bestaudio/best"
                ydl_opts['format'] = fmt

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                downloaded_file = ydl.prepare_filename(info)
                
                # If audio was converted to MP3, check for .mp3 extension
                if audio_only:
                    base_name = os.path.splitext(downloaded_file)[0]
                    mp3_path = base_name + ".mp3"
                    if os.path.exists(mp3_path):
                        downloaded_file = mp3_path

                if not os.path.exists(downloaded_file):
                    files = os.listdir(tmp_dir)
                    if files:
                        downloaded_file = os.path.join(tmp_dir, files[0])
                    else:
                        raise HTTPException(status_code=500, detail="Downloaded file was not found on server.")

                safe_title = sanitize_filename(info.get('title', 'video'))
                ext = "mp3" if audio_only else (os.path.splitext(downloaded_file)[1].lstrip('.') or 'mp4')
                raw_filename = f"{info.get('title', 'video')}.{ext}"
                ascii_filename = f"{safe_title}.{ext}"
                utf8_encoded_filename = urllib.parse.quote(raw_filename)
                filesize = os.path.getsize(downloaded_file)

                def iterfile():
                    try:
                        with open(downloaded_file, mode="rb") as f:
                            while chunk := f.read(1024 * 256):  # 256KB chunks
                                yield chunk
                    finally:
                        try:
                            if os.path.exists(downloaded_file):
                                os.remove(downloaded_file)
                            if os.path.exists(tmp_dir):
                                os.rmdir(tmp_dir)
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
