import os
import json
import yt_dlp
from flask import Flask, request, jsonify, Response, stream_with_context
import requests

app = Flask(__name__)

def get_ydl_opts(quality="best"):
    fmt = "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best"
    if quality == "audio":
        fmt = "bestaudio[ext=m4a]/bestaudio"
    return {
        "format": fmt,
        "quiet": True,
        "no_warnings": True,
        "extract_flat": False,
    }

@app.route("/info", methods=["POST"])
def info():
    data = request.get_json()
    url = data.get("url", "")
    if not url:
        return jsonify({"error": "No URL provided"}), 400

    try:
        opts = {"quiet": True, "no_warnings": True}
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
            return jsonify({
                "title": info.get("title", ""),
                "duration": info.get("duration_string", ""),
                "thumbnail": info.get("thumbnail", ""),
                "extractor": info.get("extractor", ""),
            })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/download", methods=["POST"])
def download():
    data = request.get_json()
    url = data.get("url", "")
    quality = data.get("quality", "best")

    if not url:
        return jsonify({"error": "No URL provided"}), 400

    try:
        opts = get_ydl_opts(quality)
        opts["listformats"] = False

        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)

            # Получаем прямую ссылку
            if "url" in info:
                direct_url = info["url"]
            elif "formats" in info:
                # Выбираем лучший mp4 формат
                formats = info["formats"]
                mp4_formats = [f for f in formats if f.get("ext") == "mp4" and f.get("url")]
                if mp4_formats:
                    direct_url = mp4_formats[-1]["url"]
                else:
                    direct_url = formats[-1].get("url", "")
            else:
                return jsonify({"error": "No download URL found"}), 500

            # Определяем тип контента
            ext = info.get("ext", "mp4")
            if quality == "audio":
                ext = "m4a"

            return jsonify({
                "url": direct_url,
                "ext": ext,
                "title": info.get("title", ""),
                "http_headers": info.get("http_headers", {})
            })

    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/proxy", methods=["POST"])
def proxy():
    """Проксирует файл через сервер чтобы обойти CORS и авторизацию"""
    data = request.get_json()
    url = data.get("url", "")
    headers = data.get("headers", {})

    if not url:
        return jsonify({"error": "No URL"}), 400

    try:
        r = requests.get(url, headers=headers, stream=True, timeout=30)
        content_type = r.headers.get("Content-Type", "video/mp4")

        def generate():
            for chunk in r.iter_content(chunk_size=8192):
                yield chunk

        return Response(
            stream_with_context(generate()),
            content_type=content_type,
            headers={"Content-Disposition": f"attachment"}
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/")
def index():
    return jsonify({"status": "ok", "service": "yt-dlp API"})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
