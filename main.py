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
    return jsonify({"status": "ok", "service": "yt-dlp proxy v14"})

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

def download_youtube(url, tmpdir):
    visitor_data = os.environ.get("YT_VISITOR_DATA", "")

    # Способ 1: pytubefix — работает без cookies и токенов
    print("Trying pytubefix...", flush=True)
    try:
        from pytubefix import YouTube
        from pytubefix.exceptions import AgeRestrictedError, VideoUnavailable

        yt = YouTube(url)
        print(f"Title: {yt.title}", flush=True)

        # Лучшее прогрессивное видео (видео+аудио в одном файле)
        stream = (yt.streams
                  .filter(progressive=True, file_extension="mp4")
                  .order_by("resolution")
                  .last())

        if not stream:
            stream = yt.streams.filter(file_extension="mp4").first()

        if stream:
            print(f"Stream: {stream.resolution} {stream.mime_type}", flush=True)
            fp = stream.download(output_path=tmpdir, filename="video.mp4")
            size = os.path.getsize(fp)
            print(f"Downloaded: {size} bytes", flush=True)
            if size > 0:
                return fp, None
            print("File is 0 bytes, trying next method", flush=True)

    except Exception as e:
        print(f"pytubefix failed: {type(e).__name__}: {e}", flush=True)

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

    for client in ["android", "ios", "web"]:
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
        if os.path.exists(COOKIES):
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
