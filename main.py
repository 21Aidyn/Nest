import os
import tempfile
import subprocess
import yt_dlp
from flask import Flask, request, jsonify, Response, stream_with_context

app = Flask(__name__)

@app.route("/")
def index():
    return jsonify({"status": "ok", "service": "yt-dlp proxy v4"})

@app.route("/test")
def test():
    result = {"yt_dlp": yt_dlp.version.__version__}
    try:
        r = subprocess.run(["ffmpeg", "-version"], capture_output=True, timeout=5)
        result["ffmpeg"] = "ok" if r.returncode == 0 else "not found"
    except Exception:
        result["ffmpeg"] = "not found"
    return jsonify(result)

@app.route("/download", methods=["POST"])
def download():
    data = request.get_json() or {}
    url = data.get("url", "").strip()
    if not url:
        return jsonify({"error": "No URL"}), 400

    with tempfile.TemporaryDirectory() as tmpdir:
        raw_path = os.path.join(tmpdir, "raw.%(ext)s")
        out_path = os.path.join(tmpdir, "output.mp4")

        # Скачиваем в лучшем доступном качестве
        # Ищем cookies.txt рядом с main.py
        base_dir = os.path.dirname(os.path.abspath(__file__))
        cookies_file = os.path.join(base_dir, "cookies.txt")

        opts = {
            "format": "bestvideo+bestaudio/best",
            "outtmpl": raw_path,
            "quiet": True,
            "no_warnings": True,
            "nocheckcertificate": True,
            "extractor_args": {
                "youtube": {"player_client": ["ios", "android"]},
            },
            "http_headers": {
                "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15",
            },
        }
        if os.path.exists(cookies_file):
            opts["cookiefile"] = cookies_file
            print(f"Using cookies: {cookies_file}", flush=True)
        else:
            print("No cookies.txt found — YouTube may block", flush=True)

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

        # Находим скачанный файл
        files = [f for f in os.listdir(tmpdir)
                 if f.startswith("raw.") and not f.endswith(".part")]
        if not files:
            return jsonify({"error": "No file downloaded"}), 500

        raw_file = os.path.join(tmpdir, files[0])
        raw_ext = files[0].rsplit(".", 1)[-1].lower()
        raw_size = os.path.getsize(raw_file)
        print(f"Downloaded: {files[0]} ({raw_size} bytes)", flush=True)

        if raw_size == 0:
            return jsonify({"error": "Downloaded file is empty"}), 500

        # Определяем — это видео или фото
        image_exts = {"jpg", "jpeg", "png", "gif", "webp"}
        if raw_ext in image_exts:
            # Фото — отдаём как есть
            ct = "image/jpeg" if raw_ext in ("jpg","jpeg") else \
                 "image/png"  if raw_ext == "png" else \
                 "image/gif"  if raw_ext == "gif" else "image/jpeg"
            def gen_img(p):
                with open(p, "rb") as f:
                    while chunk := f.read(65536):
                        yield chunk
            return Response(
                stream_with_context(gen_img(raw_file)),
                content_type=ct,
                headers={
                    "Content-Length": str(raw_size),
                    "X-File-Ext": raw_ext,
                    "Content-Disposition": f'attachment; filename="media.{raw_ext}"',
                }
            )

        # Видео — конвертируем в H.264 mp4 через ffmpeg
        # -movflags +faststart нужен чтобы iOS мог начать воспроизведение
        print("Converting to H.264 mp4...", flush=True)
        ffmpeg_cmd = [
            "ffmpeg", "-i", raw_file,
            "-c:v", "libx264",
            "-preset", "fast",
            "-crf", "23",
            "-c:a", "aac",
            "-b:a", "128k",
            "-movflags", "+faststart",
            "-pix_fmt", "yuv420p",  # совместимость с iOS
            "-y", out_path
        ]
        result = subprocess.run(ffmpeg_cmd, capture_output=True, timeout=300)

        if result.returncode != 0 or not os.path.exists(out_path) or os.path.getsize(out_path) == 0:
            err = result.stderr.decode()[-300:] if result.stderr else "ffmpeg failed"
            print(f"ffmpeg error: {err}", flush=True)
            # Отдаём оригинал если конвертация упала
            out_path = raw_file
            ext = raw_ext
        else:
            ext = "mp4"

        filesize = os.path.getsize(out_path)
        print(f"Output: {ext} ({filesize} bytes)", flush=True)

        def generate(path):
            with open(path, "rb") as f:
                while chunk := f.read(65536):
                    yield chunk

        return Response(
            stream_with_context(generate(out_path)),
            content_type="video/mp4",
            headers={
                "Content-Length": str(filesize),
                "X-File-Ext": ext,
                "Content-Disposition": f'attachment; filename="media.{ext}"',
            }
        )

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
