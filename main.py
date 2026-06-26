import os
import re
import tempfile
import shutil
import requests
import yt_dlp
from flask import Flask, request, jsonify, Response, stream_with_context

app = Flask(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
COOKIES = os.path.join(BASE_DIR, "cookies.txt")
RAPIDAPI_KEY = "64c89254f7mshd0cefa24f361f2ep17ca53jsn6ec5c02992af"

@app.route("/")
def index():
    return jsonify({"status": "ok", "service": "yt-dlp proxy v16"})

@app.route("/test")
def test():
    result = {"yt_dlp": yt_dlp.version.__version__}
    try:
        import pytubefix
        result["pytubefix"] = pytubefix.__version__
    except Exception:
        result["pytubefix"] = "not installed"
    return jsonify(result)

def is_youtube(url):
    return "youtube.com" in url or "youtu.be" in url

def extract_video_id(url):
    for p in [r'youtu\.be/([a-zA-Z0-9_-]{11})',
              r'youtube\.com/watch\?v=([a-zA-Z0-9_-]{11})',
              r'youtube\.com/shorts/([a-zA-Z0-9_-]{11})',
              r'youtube\.com/embed/([a-zA-Z0-9_-]{11})']:
        m = re.search(p, url)
        if m:
            return m.group(1)
    return None

def download_youtube(url, tmpdir):
    video_id = extract_video_id(url)
    if not video_id:
        return None, "Cannot extract video ID"

    print(f"Video ID: {video_id}", flush=True)

    # Способ 1: YTStream через RapidAPI — стримим файл через их сервер
    print("Trying YTStream stream endpoint...", flush=True)
    try:
        headers = {
            "x-rapidapi-key": RAPIDAPI_KEY,
            "x-rapidapi-host": "ytstream-download-youtube-videos.p.rapidapi.com"
        }

        # Сначала получаем метаданные
        meta_resp = requests.get(
            "https://ytstream-download-youtube-videos.p.rapidapi.com/dl",
            headers=headers,
            params={"id": video_id},
            timeout=30
        )
        print(f"YTStream meta status: {meta_resp.status_code}", flush=True)
        meta = meta_resp.json()

        # Ищем формат — берём 360p или 480p (прогрессивный, без merge)
        formats = meta.get("formats", [])
        chosen = None
        for quality in ["360p", "480p", "240p", "720p"]:
            for f in formats:
                label = f.get("qualityLabel", "")
                mime = f.get("mimeType", "")
                if label == quality and "video/mp4" in mime and f.get("url"):
                    chosen = f
                    break
            if chosen:
                break

        if not chosen and formats:
            chosen = next((f for f in formats if "video/mp4" in f.get("mimeType","") and f.get("url")), None)

        if not chosen:
            print(f"No format found. Available: {[f.get('qualityLabel') for f in formats]}", flush=True)
        else:
            print(f"Chosen: {chosen.get('qualityLabel')} {chosen.get('mimeType')}", flush=True)
            direct_url = chosen["url"]

            # Скачиваем с правильными заголовками
            dl_headers = {
                "User-Agent": "Mozilla/5.0 (Linux; Android 11; Pixel 5) AppleWebKit/537.36",
                "Referer": "https://www.youtube.com/",
                "Origin": "https://www.youtube.com",
            }
            # Добавляем заголовки из ответа YTStream если есть
            if chosen.get("approxDurationMs"):
                pass  # just metadata

            out = os.path.join(tmpdir, "video.mp4")
            r = requests.get(direct_url, headers=dl_headers, stream=True, timeout=120, allow_redirects=True)
            print(f"CDN status: {r.status_code}", flush=True)

            if r.status_code == 200:
                with open(out, "wb") as f:
                    for chunk in r.iter_content(65536):
                        f.write(chunk)
                size = os.path.getsize(out)
                print(f"Downloaded: {size} bytes", flush=True)
                if size > 0:
                    return out, None
            else:
                print(f"CDN error: {r.status_code}", flush=True)

    except Exception as e:
        print(f"YTStream failed: {type(e).__name__}: {e}", flush=True)

    # Способ 2: yt-dlp с android клиентом (без cookies)
    print("Trying yt-dlp...", flush=True)
    raw_path = os.path.join(tmpdir, "raw.%(ext)s")
    visitor_data = os.environ.get("YT_VISITOR_DATA", "")

    for client in ["android", "ios"]:
        opts = {
            "format": "best[ext=mp4]/best",
            "outtmpl": raw_path,
            "quiet": True,
            "nocheckcertificate": True,
            "merge_output_format": "mp4",
            "extractor_args": {
                "youtube": {
                    "player_client": [client],
                    **({"visitor_data": [visitor_data]} if visitor_data else {}),
                }
            },
        }
        # android не поддерживает cookies — пропускаем
        if client != "android" and os.path.exists(COOKIES):
            opts["cookiefile"] = COOKIES

        try:
            print(f"yt-dlp client={client}", flush=True)
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.download([url])
            files = [f for f in os.listdir(tmpdir)
                     if f.startswith("raw.") and not f.endswith(".part")]
            for f in files:
                fp = os.path.join(tmpdir, f)
                sz = os.path.getsize(fp)
                if sz > 0:
                    print(f"yt-dlp success: {f} {sz} bytes", flush=True)
                    return fp, None
        except Exception as e:
            print(f"yt-dlp {client} failed: {e}", flush=True)
            for f in os.listdir(tmpdir):
                if f.startswith("raw."):
                    try: os.remove(os.path.join(tmpdir, f))
                    except: pass

    return None, "All YouTube methods failed"

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
    # Не используем cookies — они могут быть устаревшими
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
    print(f"\n=== Downloading: {url} ===", flush=True)

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
    print(f"Final: {os.path.basename(filepath)} ({filesize} bytes)", flush=True)

    if filesize == 0:
        shutil.rmtree(tmpdir, ignore_errors=True)
        return jsonify({"error": "File is empty"}), 500

    image_exts = {"jpg", "jpeg", "png", "gif", "webp"}
    ct = "image/jpeg" if ext in ("jpg","jpeg") else \
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
