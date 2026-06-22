import os
import tempfile
import yt_dlp
from flask import Flask, request, jsonify, Response, stream_with_context

app = Flask(__name__)

COOKIES_FILE = "cookies.txt"  # опционально

def make_ydl_opts(tmpdir, audio_only=False):
    fmt = "bestaudio/best" if audio_only else "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best"
    opts = {
        "format": fmt,
        "outtmpl": os.path.join(tmpdir, "%(title).50s.%(ext)s"),
        "quiet": True,
        "no_warnings": True,
        "merge_output_format": "mp4",
        "postprocessors": [] if audio_only else [],
        # Обходим ограничения
        "nocheckcertificate": True,
        "extractor_args": {
            "youtube": {
                "player_client": ["android", "web"],
            }
        },
        "http_headers": {
            "User-Agent": "Mozilla/5.0 (Linux; Android 11; Pixel 5) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/90.0.4430.91 Mobile Safari/537.36",
        },
    }
    if os.path.exists(COOKIES_FILE):
        opts["cookiefile"] = COOKIES_FILE
    return opts

@app.route("/")
def index():
    return jsonify({"status": "ok", "service": "yt-dlp proxy"})

@app.route("/download", methods=["POST"])
def download():
    data = request.get_json()
    url = data.get("url", "").strip()
    quality = data.get("quality", "best")

    if not url:
        return jsonify({"error": "No URL provided"}), 400

    audio_only = quality == "audio"

    with tempfile.TemporaryDirectory() as tmpdir:
        opts = make_ydl_opts(tmpdir, audio_only=audio_only)

        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=True)

            # Ищем скачанный файл
            files = [f for f in os.listdir(tmpdir) if not f.endswith(".part")]
            if not files:
                return jsonify({"error": "No file downloaded"}), 500

            filepath = os.path.join(tmpdir, files[0])
            ext = files[0].rsplit(".", 1)[-1].lower()
            filename = files[0]

            # Стримим файл клиенту
            def generate():
                with open(filepath, "rb") as f:
                    while chunk := f.read(65536):
                        yield chunk

            content_type = "video/mp4" if ext in ("mp4", "mkv", "webm") else \
                           "audio/mpeg" if ext == "mp3" else \
                           "audio/mp4" if ext == "m4a" else \
                           "image/jpeg" if ext in ("jpg", "jpeg") else \
                           "application/octet-stream"

            return Response(
                stream_with_context(generate()),
                content_type=content_type,
                headers={
                    "Content-Disposition": f'attachment; filename="{filename}"',
                    "Content-Length": str(os.path.getsize(filepath)),
                    "X-File-Ext": ext,
                }
            )

        except yt_dlp.utils.DownloadError as e:
            msg = str(e)
            print(f"DownloadError: {msg}")
            return jsonify({"error": msg}), 500
        except Exception as e:
            print(f"Error: {e}")
            return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
