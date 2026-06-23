import os
import tempfile
import shutil
import yt_dlp
from flask import Flask, request, jsonify, Response, stream_with_context

app = Flask(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
COOKIES = os.path.join(BASE_DIR, "cookies.txt")

@app.route("/")
def index():
    return jsonify({"status": "ok", "service": "yt-dlp proxy v15"})

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

RAPIDAPI_KEY = "64c89254f7mshd0cefa24f361f2ep17ca53jsn6ec5c02992af"

def download_youtube_ytstream(url, tmpdir):
    """Скачиваем через YTStream RapidAPI"""
    import re
    import requests

    # Извлекаем video ID
    patterns = [
        r'youtu\.be/([a-zA-Z0-9_-]{11})',
        r'youtube\.com/watch\?v=([a-zA-Z0-9_-]{11})',
        r'youtube\.com/shorts/([a-zA-Z0-9_-]{11})',
    ]
    video_id = None
    for p in patterns:
        m = re.search(p, url)
        if m:
            video_id = m.group(1)
            break

    if not video_id:
        return None, "Cannot extract video ID"

    print(f"YTStream: video_id={video_id}", flush=True)

    headers = {
        "x-rapidapi-key": RAPIDAPI_KEY,
        "x-rapidapi-host": "ytstream-download-youtube-videos.p.rapidapi.com"
    }
    params = {"id": video_id}

    try:
        resp = requests.get(
            "https://ytstream-download-youtube-videos.p.rapidapi.com/dl",
            headers=headers,
            params=params,
            timeout=30
        )
        print(f"YTStream status: {resp.status_code}", flush=True)
        data = resp.json()
        print(f"YTStream response keys: {list(data.keys())}", flush=True)

        # Ищем прямую ссылку на mp4
        formats = data.get("formats", [])
        link = None
        # Берём лучший mp4 с видео+аудио
        for f in sorted(formats, key=lambda x: int(x.get("qualityLabel","0p").replace("p","")), reverse=True):
            if f.get("mimeType","").startswith("video/mp4") and f.get("url"):
                link = f["url"]
                print(f"Found format: {f.get('qualityLabel')} {f.get('mimeType')}", flush=True)
                break

        if not link:
            # Fallback на adaptiveFormats
            adaptive = data.get("adaptiveFormats", [])
            for f in sorted(adaptive, key=lambda x: int(x.get("qualityLabel","0p").replace("p","")), reverse=True):
                if f.get("mimeType","").startswith("video/mp4") and f.get("url"):
                    link = f["url"]
                    print(f"Found adaptive: {f.get('qualityLabel')}", flush=True)
                    break

        if not link:
            return None, f"No mp4 URL in YTStream response: {list(data.keys())}"

        # Скачиваем файл
        out = os.path.join(tmpdir, "video.mp4")
        print(f"Downloading from CDN...", flush=True)
        r = requests.get(link, stream=True, timeout=120)
        r.raise_for_status()
        with open(out, "wb") as f:
            for chunk in r.iter_content(65536):
                f.write(chunk)
        size = os.path.getsize(out)
        print(f"Downloaded: {size} bytes", flush=True)
        if size > 0:
            return out, None
        return None, "Downloaded file is empty"

    except Exception as e:
        return None, f"YTStream error: {type(e).__name__}: {e}"


def download_youtube(url, tmpdir):
    # Способ 1: YTStream RapidAPI
    print("Trying YTStream...", flush=True)
    fp, err = download_youtube_ytstream(url, tmpdir)
    if fp:
        return fp, None
    print(f"YTStream failed: {err}", flush=True)

    # Способ 2: yt-dlp с android клиентом
    print("Trying yt-dlp android...", flush=True)
    raw_path = os.path.join(tmpdir, "raw.%(ext)s")

    # Находим node для JS runtime
    import subprocess
    node_path = ""
    for p in ["/usr/bin/node", "/usr/local/bin/node", "/opt/render/project/src/.venv/bin/node"]:
        if os.path.exists(p):
            node_path = p
            break
    if not node_path:
        try:
            r = subprocess.run(["which", "node"], capture_output=True, text=True)
            node_path = r.stdout.strip()
        except Exception:
            pass
    print(f"Node path: {node_path}", flush=True)

    for client, use_cookies in [("mweb", True), ("web_creator", True), ("ios", False)]:
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
        if node_path:
            opts["js_runtimes"] = f"nodejs:{node_path}"
        # Cookies только для клиентов которые их поддерживают
        if use_cookies and os.path.exists(COOKIES):
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
                print(f"  {f}: {sz} bytes", flush=True)
                if sz > 0:
                    return fp, None
        except Exception as e:
            print(f"yt-dlp {client} failed: {e}", flush=True)
            # Удаляем частичные файлы перед следующей попыткой
            for f in os.listdir(tmpdir):
                if f.startswith("raw."):
                    try:
                        os.remove(os.path.join(tmpdir, f))
                    except Exception:
                        pass

    return None, "All YouTube download methods failed"

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
    print(f"Final: {os.path.basename(filepath)} ({filesize} bytes)", flush=True)

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
