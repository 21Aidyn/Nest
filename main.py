import os
import re
import json
import tempfile
import shutil
import requests
import yt_dlp
from flask import Flask, request, jsonify, Response, stream_with_context

app = Flask(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
COOKIES = os.path.join(BASE_DIR, "cookies.txt")

@app.route("/")
def index():
    return jsonify({"status": "ok", "service": "yt-dlp proxy v12"})

@app.route("/test")
def test():
    result = {"yt_dlp": yt_dlp.version.__version__}
    result["visitor_data"] = bool(os.environ.get("YT_VISITOR_DATA"))
    try:
        import pytubefix
        result["pytubefix"] = pytubefix.__version__
    except Exception:
        result["pytubefix"] = "not installed"
    return jsonify(result)

def is_youtube(url):
    return "youtube.com" in url or "youtu.be" in url

def extract_video_id(url):
    patterns = [
        r'youtu\.be/([a-zA-Z0-9_-]{11})',
        r'youtube\.com/watch\?v=([a-zA-Z0-9_-]{11})',
        r'youtube\.com/shorts/([a-zA-Z0-9_-]{11})',
        r'youtube\.com/embed/([a-zA-Z0-9_-]{11})',
    ]
    for p in patterns:
        m = re.search(p, url)
        if m:
            return m.group(1)
    return None

def get_youtube_stream_url(video_id):
    """Получаем прямую ссылку через YouTube innertube API"""
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15",
        "Origin": "https://www.youtube.com",
        "Referer": "https://www.youtube.com/",
    }

    # iOS клиент — меньше всего проверок
    payload = {
        "videoId": video_id,
        "context": {
            "client": {
                "clientName": "IOS",
                "clientVersion": "19.29.1",
                "deviceModel": "iPhone16,2",
                "osName": "iPhone",
                "osVersion": "17.5.1.21F90",
                "hl": "en",
                "gl": "US",
            }
        }
    }

    visitor_data = os.environ.get("YT_VISITOR_DATA", "")
    if visitor_data:
        payload["context"]["client"]["visitorData"] = visitor_data

    resp = requests.post(
        "https://www.youtube.com/youtubei/v1/player?key=AIzaSyB-63vPrdThhKuerbB2N_l7Kwwcxj6yUAc",
        json=payload,
        headers=headers,
        timeout=15
    )
    data = resp.json()

    formats = data.get("streamingData", {}).get("formats", [])
    adaptive = data.get("streamingData", {}).get("adaptiveFormats", [])

    print(f"formats: {len(formats)}, adaptive: {len(adaptive)}", flush=True)

    # Ищем лучший прогрессивный mp4 (видео+аудио в одном)
    best = None
    for f in formats:
        if f.get("mimeType", "").startswith("video/mp4"):
            if best is None or f.get("bitrate", 0) > best.get("bitrate", 0):
                best = f

    if best and best.get("url"):
        print(f"Found stream: {best.get('qualityLabel')} {best.get('mimeType')}", flush=True)
        return best["url"], best.get("qualityLabel", ""), None

    # Если нет прогрессивного — берём лучший адаптивный mp4
    for f in adaptive:
        if f.get("mimeType", "").startswith("video/mp4") and f.get("url"):
            print(f"Found adaptive: {f.get('qualityLabel')} {f.get('mimeType')}", flush=True)
            return f["url"], f.get("qualityLabel", ""), None

    error = data.get("playabilityStatus", {}).get("reason", "No stream found")
    return None, None, error

def download_youtube(url, tmpdir):
    video_id = extract_video_id(url)
    if not video_id:
        return None, "Cannot extract video ID"

    print(f"Video ID: {video_id}", flush=True)

    # Способ 1: YouTube innertube API напрямую
    stream_url, quality, error = get_youtube_stream_url(video_id)
    if stream_url:
        print(f"Downloading stream directly: {quality}", flush=True)
        out_path = os.path.join(tmpdir, "video.mp4")
        headers = {
            "User-Agent": "com.google.ios.youtube/19.29.1 (iPhone16,2; U; CPU iOS 17_5_1 like Mac OS X;)",
            "Referer": "https://www.youtube.com/",
        }
        try:
            r = requests.get(stream_url, headers=headers, stream=True, timeout=60)
            r.raise_for_status()
            with open(out_path, "wb") as f:
                for chunk in r.iter_content(chunk_size=65536):
                    f.write(chunk)
            size = os.path.getsize(out_path)
            print(f"Downloaded: {size} bytes", flush=True)
            if size > 0:
                return out_path, None
        except Exception as e:
            print(f"Direct download failed: {e}", flush=True)

    # Способ 2: yt-dlp с visitor_data
    print("Trying yt-dlp...", flush=True)
    raw_path = os.path.join(tmpdir, "raw.%(ext)s")
    visitor_data = os.environ.get("YT_VISITOR_DATA", "")

    opts = {
        "format": "best[ext=mp4]/best",
        "outtmpl": raw_path,
        "quiet": False,
        "nocheckcertificate": True,
        "merge_output_format": "mp4",
        "extractor_args": {
            "youtube": {
                "player_client": ["ios"],
                **({"visitor_data": [visitor_data]} if visitor_data else {}),
            }
        },
    }
    if os.path.exists(COOKIES):
        opts["cookiefile"] = COOKIES

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([url])
        files = [f for f in os.listdir(tmpdir)
                 if f.startswith("raw.") and not f.endswith(".part")]
        print(f"yt-dlp files: {files}", flush=True)
        for f in files:
            fp = os.path.join(tmpdir, f)
            sz = os.path.getsize(fp)
            print(f"  {f}: {sz} bytes", flush=True)
            if sz > 0:
                return fp, None
    except Exception as e:
        print(f"yt-dlp error: {e}", flush=True)

    # Способ 3: pytubefix
    print("Trying pytubefix...", flush=True)
    try:
        from pytubefix import YouTube
        yt = YouTube(f"https://www.youtube.com/watch?v={video_id}")
        stream = (yt.streams.filter(progressive=True, file_extension="mp4")
                  .order_by("resolution").last())
        if not stream:
            stream = yt.streams.filter(file_extension="mp4").first()
        if stream:
            fp = stream.download(output_path=tmpdir, filename="video.mp4")
            if os.path.getsize(fp) > 0:
                return fp, None
    except Exception as e:
        print(f"pytubefix error: {e}", flush=True)

    return None, error or "All methods failed"

def download_generic(url, tmpdir):
    raw_path = os.path.join(tmpdir, "raw.%(ext)s")
    opts = {
        "format": "best[ext=mp4]/best",
        "outtmpl": raw_path,
        "quiet": True,
        "nocheckcertificate": True,
        "merge_output_format": "mp4",
        "http_headers": {
            "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15",
        },
    }
    if os.path.exists(COOKIES):
        opts["cookiefile"] = COOKIES
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([url])
    except Exception as e:
        return None, str(e)
    files = [f for f in os.listdir(tmpdir)
             if f.startswith("raw.") and not f.endswith(".part")]
    if not files:
        return None, "No file downloaded"
    return os.path.join(tmpdir, files[0]), None

@app.route("/download", methods=["POST"])
def download():
    data = request.get_json() or {}
    url = data.get("url", "").strip()
    if not url:
        return jsonify({"error": "No URL"}), 400

    tmpdir = tempfile.mkdtemp()
    print(f"Downloading: {url}", flush=True)

    if is_youtube(url):
        filepath, error = download_youtube(url, tmpdir)
    else:
        filepath, error = download_generic(url, tmpdir)

    if error or not filepath:
        shutil.rmtree(tmpdir, ignore_errors=True)
        print(f"Error: {error}", flush=True)
        return jsonify({"error": error or "Download failed"}), 500

    if not os.path.exists(filepath):
        shutil.rmtree(tmpdir, ignore_errors=True)
        return jsonify({"error": "File not found"}), 500

    ext = filepath.rsplit(".", 1)[-1].lower() if "." in filepath else "mp4"
    filesize = os.path.getsize(filepath)
    print(f"Final file: {os.path.basename(filepath)} ({filesize} bytes)", flush=True)

    if filesize == 0:
        shutil.rmtree(tmpdir, ignore_errors=True)
        return jsonify({"error": "File is empty"}), 500

    image_exts = {"jpg", "jpeg", "png", "gif", "webp"}
    ct = "image/jpeg" if ext in ("jpg", "jpeg") else \
         f"image/{ext}" if ext in image_exts else "video/mp4"
    if ext not in image_exts:
        ext = "mp4"

    def generate(path, directory):
        try:
            with open(path, "rb") as f:
                while True:
                    chunk = f.read(65536)
                    if not chunk:
                        break
                    yield chunk
        finally:
            shutil.rmtree(directory, ignore_errors=True)

    return Response(
        stream_with_context(generate(filepath, tmpdir)),
        content_type=ct,
        headers={
            "Content-Length": str(filesize),
            "X-File-Ext": ext,
            "Content-Disposition": f'attachment; filename="media.{ext}"',
        }
    )

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
