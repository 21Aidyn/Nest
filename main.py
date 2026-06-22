import os
import tempfile
import subprocess
import json
import yt_dlp
from flask import Flask, request, jsonify, Response, stream_with_context

app = Flask(__name__)

def get_ydl_opts(tmpdir, audio_only=False):
    # mp4 с H.264 — единственный формат который точно играет на iOS
    if audio_only:
        fmt = "bestaudio[ext=m4a]/bestaudio"
    else:
        fmt = (
            "bestvideo[ext=mp4][vcodec^=avc1]+bestaudio[ext=m4a]"
            "/bestvideo[ext=mp4]+bestaudio[ext=m4a]"
            "/best[ext=mp4]"
            "/best"
        )

    opts = {
        "format": fmt,
        "outtmpl": os.path.join(tmpdir, "video.%(ext)s"),
        "quiet": True,
        "no_warnings": True,
        "merge_output_format": "mp4",
        "nocheckcertificate": True,
        # Обходим блокировку YouTube
        "extractor_args": {
            "youtube": {
                "player_client": ["ios", "android"],
                "player_skip": ["webpage", "configs"],
            }
        },
        "http_headers": {
            "User-Agent": "com.google.ios.youtube/19.29.1 (iPhone16,2; U; CPU iOS 17_5_1 like Mac OS X;)",
        },
        # Постпроцессор для конвертации в H.264 mp4
        "postprocessors": [{
            "key": "FFmpegVideoConvertor",
            "preferedformat": "mp4",
        }] if not audio_only else [],
    }
    return opts

@app.route("/")
def index():
    return jsonify({"status": "ok", "service": "yt-dlp proxy v2"})

@app.route("/download", methods=["POST"])
def download():
    data = request.get_json()
    url = data.get("url", "").strip()
    quality = data.get("quality", "best")

    if not url:
        return jsonify({"error": "No URL"}), 400

    audio_only = quality == "audio"

    with tempfile.TemporaryDirectory() as tmpdir:
        opts = get_ydl_opts(tmpdir, audio_only=audio_only)

        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                print(f"Downloading: {url}")
                ydl.download([url])

            # Находим скачанный файл
            files = [f for f in os.listdir(tmpdir)
                     if not f.endswith(".part") and not f.endswith(".ytdl")]
            if not files:
                return jsonify({"error": "No file downloaded"}), 500

            filepath = os.path.join(tmpdir, files[0])
            ext = files[0].rsplit(".", 1)[-1].lower() if "." in files[0] else "mp4"
            filesize = os.path.getsize(filepath)

            print(f"Downloaded: {files[0]} ({filesize} bytes)")

            # Если файл не mp4 — конвертируем через ffmpeg
            if ext != "mp4" and not audio_only:
                out_path = os.path.join(tmpdir, "converted.mp4")
                result = subprocess.run([
                    "ffmpeg", "-i", filepath,
                    "-c:v", "libx264", "-c:a", "aac",
                    "-movflags", "+faststart",
                    "-y", out_path
                ], capture_output=True, timeout=300)

                if result.returncode == 0 and os.path.exists(out_path):
                    filepath = out_path
                    ext = "mp4"
                    filesize = os.path.getsize(filepath)
                    print(f"Converted to mp4: {filesize} bytes")

            content_type = (
                "video/mp4"     if ext in ("mp4", "mkv", "webm") else
                "audio/mp4"     if ext == "m4a" else
                "audio/mpeg"    if ext == "mp3" else
                "image/jpeg"    if ext in ("jpg", "jpeg") else
                "image/png"     if ext == "png" else
                "application/octet-stream"
            )

            def generate():
                with open(filepath, "rb") as f:
                    while True:
                        chunk = f.read(65536)
                        if not chunk:
                            break
                        yield chunk

            return Response(
                stream_with_context(generate()),
                content_type=content_type,
                headers={
                    "Content-Disposition": f'attachment; filename="video.{ext}"',
                    "Content-Length": str(filesize),
                    "X-File-Ext": ext,
                }
            )

        except yt_dlp.utils.DownloadError as e:
            msg = str(e).replace("\n", " ")
            print(f"DownloadError: {msg}")
            return jsonify({"error": msg[:300]}), 500
        except subprocess.TimeoutExpired:
            return jsonify({"error": "Conversion timeout"}), 500
        except Exception as e:
            print(f"Error: {e}")
            return jsonify({"error": str(e)[:300]}), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
