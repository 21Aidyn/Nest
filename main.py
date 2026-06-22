import os
import tempfile
import subprocess
import yt_dlp
from flask import Flask, request, jsonify, Response, stream_with_context

app = Flask(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
COOKIES = os.path.join(BASE_DIR, "cookies.txt")

@app.route("/")
def index():
    return jsonify({"status": "ok", "service": "yt-dlp proxy v5"})

@app.route("/test")
def test():
    result = {"yt_dlp": yt_dlp.version.__version__}
    try:
        r = subprocess.run(["ffmpeg", "-version"], capture_output=True, timeout=5)
        result["ffmpeg"] = "ok" if r.returncode == 0 else "not found"
    except Exception:
        result["ffmpeg"] = "not found"
    result["cookies"] = os.path.exists(COOKIES)
    return jsonify(result)

@app.route("/download", methods=["POST"])
def download():
    data = request.get_json() or {}
    url = data.get("url", "").strip()
    if not url:
        return jsonify({"error": "No URL"}), 400

    with tempfile.TemporaryDirectory() as tmpdir:
        raw_path = os.path.join(tmpdir, "raw.%(ext)s")

        # Скачиваем уже в mp4/H264 напрямую — без конвертации
        # Это избегает ffmpeg и экономит память/время
        opts = {
            # Берём лучший mp4 с H264 кодеком напрямую
            "format": (
                "bestvideo[vcodec^=avc1][ext=mp4]+bestaudio[ext=m4a]"
                "/bestvideo[vcodec^=avc][ext=mp4]+bestaudio[ext=m4a]"
                "/bestvideo[ext=mp4]+bestaudio[ext=m4a]"
                "/best[ext=mp4]"
                "/best"
            ),
            "outtmpl": raw_path,
            "quiet": True,
            "no_warnings": True,
            "nocheckcertificate": True,
            "merge_output_format": "mp4",
            "postprocessors": [{
                "key": "FFmpegVideoRemuxer",
                "preferedformat": "mp4",
            }],
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
            msg = str(e).replace("\n", " ")[:500]
            print(f"DownloadError: {msg}", flush=True)
            return jsonify({"error": msg}), 500
        except Exception as e:
            print(f"Error: {e}", flush=True)
            return jsonify({"error": str(e)[:300]}), 500

        # Находим файл
        files = [f for f in os.listdir(tmpdir)
                 if f.startswith("raw.") and not f.endswith(".part") and not f.endswith(".ytdl")]
        if not files:
            return jsonify({"error": "No file downloaded"}), 500

        filepath = os.path.join(tmpdir, files[0])
        ext = files[0].rsplit(".", 1)[-1].lower() if "." in files[0] else "mp4"
        filesize = os.path.getsize(filepath)
        print(f"File: {files[0]} ({filesize} bytes)", flush=True)

        if filesize == 0:
            return jsonify({"error": "File is empty"}), 500

        # Определяем тип
        image_exts = {"jpg", "jpeg", "png", "gif", "webp"}
        if ext in image_exts:
            ct = f"image/{'jpeg' if ext in ('jpg','jpeg') else ext}"
        else:
            ct = "video/mp4"
            ext = "mp4"

        def generate(path):
            with open(path, "rb") as f:
                while chunk := f.read(65536):
                    yield chunk

        return Response(
            stream_with_context(generate(filepath)),
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
