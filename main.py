import os
import tempfile
import shutil
import subprocess
import yt_dlp
from flask import Flask, request, jsonify, Response, stream_with_context

app = Flask(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
COOKIES = os.path.join(BASE_DIR, "cookies.txt")

@app.route("/")
def index():
    return jsonify({"status": "ok", "service": "yt-dlp proxy v8"})

@app.route("/test")
def test():
    result = {"yt_dlp": yt_dlp.version.__version__, "cookies": os.path.exists(COOKIES)}
    try:
        r = subprocess.run(["ffmpeg", "-version"], capture_output=True, timeout=5)
        result["ffmpeg"] = "ok" if r.returncode == 0 else "not found"
    except Exception:
        result["ffmpeg"] = "not found"
    return jsonify(result)

def is_youtube(url):
    return "youtube.com" in url or "youtu.be" in url

def download_youtube(url, tmpdir):
    """Скачиваем YouTube через pytubefix — не требует cookies/PO token"""
    try:
        from pytubefix import YouTube
        from pytubefix.cli import on_progress

        yt = YouTube(url, on_progress_callback=on_progress, use_oauth=False, allow_oauth_cache=False)
        print(f"YouTube title: {yt.title}", flush=True)

        # Берём лучший прогрессивный поток (видео+аудио в одном файле)
        stream = yt.streams.filter(progressive=True, file_extension="mp4")\
                            .order_by("resolution").last()

        if not stream:
            # Fallback — любой mp4
            stream = yt.streams.filter(file_extension="mp4").first()

        if not stream:
            return None, "No suitable stream found"

        print(f"Stream: {stream.resolution} {stream.mime_type}", flush=True)
        filepath = stream.download(output_path=tmpdir, filename="video.mp4")
        return filepath, None

    except Exception as e:
        return None, str(e)

def download_generic(url, tmpdir):
    """Скачиваем через yt-dlp для всех остальных сайтов"""
    raw_path = os.path.join(tmpdir, "raw.%(ext)s")
    opts = {
        "format": "best[ext=mp4]/best",
        "outtmpl": raw_path,
        "quiet": True,
        "no_warnings": True,
        "nocheckcertificate": True,
        "merge_output_format": "mp4",
        "http_headers": {
            "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15",
        },
    }
    if os.path.exists(COOKIES):
        opts["cookiefile"] = COOKIES

    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([url])

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

    try:
        if is_youtube(url):
            filepath, error = download_youtube(url, tmpdir)
        else:
            filepath, error = download_generic(url, tmpdir)

        if error or not filepath:
            shutil.rmtree(tmpdir, ignore_errors=True)
            return jsonify({"error": error or "Download failed"}), 500

        if not os.path.exists(filepath):
            shutil.rmtree(tmpdir, ignore_errors=True)
            return jsonify({"error": "File not found after download"}), 500

        ext = filepath.rsplit(".", 1)[-1].lower() if "." in filepath else "mp4"
        filesize = os.path.getsize(filepath)
        print(f"File: {os.path.basename(filepath)} ({filesize} bytes)", flush=True)

        if filesize == 0:
            shutil.rmtree(tmpdir, ignore_errors=True)
            return jsonify({"error": "File is empty"}), 500

        image_exts = {"jpg", "jpeg", "png", "gif", "webp"}
        ct = f"image/{'jpeg' if ext in ('jpg','jpeg') else ext}" \
             if ext in image_exts else "video/mp4"

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
                print(f"Cleaned up: {directory}", flush=True)

        return Response(
            stream_with_context(generate(filepath, tmpdir)),
            content_type=ct,
            headers={
                "Content-Length": str(filesize),
                "X-File-Ext": ext,
                "Content-Disposition": f'attachment; filename="media.{ext}"',
            }
        )

    except yt_dlp.utils.DownloadError as e:
        shutil.rmtree(tmpdir, ignore_errors=True)
        msg = str(e).replace("\n", " ")[:500]
        print(f"DownloadError: {msg}", flush=True)
        return jsonify({"error": msg}), 500
    except Exception as e:
        shutil.rmtree(tmpdir, ignore_errors=True)
        print(f"Error: {type(e).__name__}: {e}", flush=True)
        return jsonify({"error": f"{type(e).__name__}: {str(e)[:300]}"}), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)

    opts = {
        "format": "best[ext=mp4]/best",
        "outtmpl": raw_path,
        "quiet": True,
        "no_warnings": True,
        "nocheckcertificate": True,
        "merge_output_format": "mp4",
        "extractor_args": {
            "youtube": {"player_client": ["ios", "android", "web"]},
        },
        "http_headers": {
            "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15",
        },
    }

    if os.path.exists(COOKIES):
        opts["cookiefile"] = COOKIES
        print("Using cookies", flush=True)

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            print(f"Downloading: {url}", flush=True)
            ydl.download([url])
    except yt_dlp.utils.DownloadError as e:
        import shutil; shutil.rmtree(tmpdir, ignore_errors=True)
        msg = str(e).replace("\n", " ")[:500]
        print(f"DownloadError: {msg}", flush=True)
        return jsonify({"error": msg}), 500
    except Exception as e:
        import shutil; shutil.rmtree(tmpdir, ignore_errors=True)
        print(f"Error: {e}", flush=True)
        return jsonify({"error": str(e)[:300]}), 500

    # Находим файл
    files = [f for f in os.listdir(tmpdir)
             if f.startswith("raw.") and not f.endswith(".part") and not f.endswith(".ytdl")]
    if not files:
        import shutil; shutil.rmtree(tmpdir, ignore_errors=True)
        return jsonify({"error": "No file downloaded"}), 500

    filepath = os.path.join(tmpdir, files[0])
    ext = files[0].rsplit(".", 1)[-1].lower() if "." in files[0] else "mp4"
    filesize = os.path.getsize(filepath)
    print(f"File: {files[0]} ({filesize} bytes)", flush=True)

    if filesize == 0:
        import shutil; shutil.rmtree(tmpdir, ignore_errors=True)
        return jsonify({"error": "File is empty"}), 500

    image_exts = {"jpg", "jpeg", "png", "gif", "webp"}
    if ext in image_exts:
        ct = f"image/{'jpeg' if ext in ('jpg','jpeg') else ext}"
    else:
        ct = "video/mp4"
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
            # Удаляем папку только после завершения стриминга
            import shutil
            shutil.rmtree(directory, ignore_errors=True)
            print(f"Cleaned up: {directory}", flush=True)

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
