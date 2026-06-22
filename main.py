import os
import tempfile
import subprocess
import yt_dlp
from flask import Flask, request, jsonify, Response, stream_with_context

app = Flask(__name__)

@app.route("/")
def index():
    return jsonify({"status": "ok", "service": "yt-dlp proxy v3"})

@app.route("/test", methods=["GET"])
def test():
    """Проверяем доступность yt-dlp и ffmpeg"""
    result = {}
    try:
        import yt_dlp
        result["yt_dlp"] = yt_dlp.version.__version__
    except Exception as e:
        result["yt_dlp_error"] = str(e)

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
    quality = data.get("quality", "best")

    if not url:
        return jsonify({"error": "No URL"}), 400

    audio_only = quality == "audio"

    # Формат: H.264 mp4 — единственный что играет на iOS
    if audio_only:
        fmt = "bestaudio[ext=m4a]/bestaudio"
    else:
        fmt = (
            "bestvideo[vcodec^=avc][ext=mp4]+bestaudio[ext=m4a]"
            "/bestvideo[vcodec^=avc]+bestaudio"
            "/best[ext=mp4]"
            "/best"
        )

    with tempfile.TemporaryDirectory() as tmpdir:
        outpath = os.path.join(tmpdir, "video.%(ext)s")
        opts = {
            "format": fmt,
            "outtmpl": outpath,
            "quiet": False,
            "no_warnings": False,
            "merge_output_format": "mp4",
            "nocheckcertificate": True,
            "extractor_args": {
                "youtube": {
                    "player_client": ["ios"],
                }
            },
            "http_headers": {
                "User-Agent": "com.google.ios.youtube/19.29.1 (iPhone16,2; U; CPU iOS 17_5_1 like Mac OS X;)",
            },
        }

        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                print(f"[download] Starting: {url}", flush=True)
                ydl.download([url])
                print("[download] Done", flush=True)

            files = [f for f in os.listdir(tmpdir)
                     if not f.endswith(".part") and not f.endswith(".ytdl") and os.path.getsize(os.path.join(tmpdir, f)) > 0]

            if not files:
                return jsonify({"error": "No file downloaded — check server logs"}), 500

            filepath = os.path.join(tmpdir, files[0])
            ext = files[0].rsplit(".", 1)[-1].lower() if "." in files[0] else "mp4"
            filesize = os.path.getsize(filepath)
            print(f"[download] File: {files[0]} ({filesize} bytes)", flush=True)

            # Если не mp4 и есть ffmpeg — конвертируем
            if ext not in ("mp4",) and not audio_only:
                converted = os.path.join(tmpdir, "out.mp4")
                r = subprocess.run([
                    "ffmpeg", "-i", filepath,
                    "-c:v", "libx264", "-preset", "fast",
                    "-c:a", "aac", "-movflags", "+faststart",
                    "-y", converted
                ], capture_output=True, timeout=300)
                if r.returncode == 0 and os.path.getsize(converted) > 0:
                    filepath = converted
                    ext = "mp4"
                    filesize = os.path.getsize(filepath)

            content_type = (
                "video/mp4"  if ext in ("mp4",) else
                "audio/mp4"  if ext == "m4a" else
                "audio/mpeg" if ext == "mp3" else
                "image/jpeg" if ext in ("jpg", "jpeg") else
                "image/png"  if ext == "png" else
                "video/mp4"
            )

            def generate(path):
                with open(path, "rb") as f:
                    while True:
                        chunk = f.read(65536)
                        if not chunk:
                            break
                        yield chunk

            return Response(
                stream_with_context(generate(filepath)),
                content_type=content_type,
                headers={
                    "Content-Disposition": f'attachment; filename="media.{ext}"',
                    "Content-Length": str(filesize),
                    "X-File-Ext": ext,
                }
            )

        except yt_dlp.utils.DownloadError as e:
            msg = str(e).replace("\n", " ")[:500]
            print(f"[error] DownloadError: {msg}", flush=True)
            return jsonify({"error": msg}), 500
        except Exception as e:
            print(f"[error] {type(e).__name__}: {e}", flush=True)
            return jsonify({"error": f"{type(e).__name__}: {str(e)[:300]}"}), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
