import os
import io
import uuid
import tempfile
import subprocess
from flask import Flask, request, jsonify, send_file, render_template
from flask_cors import CORS
from pydub import AudioSegment
from PIL import Image
from docx import Document
from werkzeug.utils import secure_filename

app = Flask(__name__)
CORS(app)

UPLOAD_FOLDER = "uploads"
COMPRESSED_FOLDER = "compressed"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(COMPRESSED_FOLDER, exist_ok=True)

# ── Compression settings (same as config.py) ──────────────────────────────────
AUDIO_CHANNELS     = 1
AUDIO_SAMPLE_RATE  = 44100
AUDIO_FORMAT       = "mp3"

VIDEO_FPS             = 24
VIDEO_CODEC           = "libx264"
VIDEO_PRESET          = "ultrafast"
VIDEO_PROFILE         = "main"
VIDEO_AUDIO_CODEC     = "aac"
VIDEO_AUDIO_BITRATE   = "64k"
VIDEO_AUDIO_CHANNELS  = 1
VIDEO_AUDIO_SAMPLE_RATE = 44100

IMAGE_MAX_SIZE  = (1920, 1080)
IMAGE_FORMAT    = "JPEG"

QUALITY_PRESETS = {
    "audio": {
        "fast":     {"bitrate": "48k"},
        "balanced": {"bitrate": "24k"},
    },
    "video": {
        "fast":     {"crf": 28, "bitrate": "150k"},
        "balanced": {"crf": 35, "bitrate": "80k"},
    },
    "image": {
        "fast":     {"quality": 60},
        "balanced": {"quality": 35},
    },
    "pdf": {
        "fast":     {"setting": "/ebook"},
        "balanced": {"setting": "/screen"},
    },
    "docx": {
        "fast":     {"quality": 55},
        "balanced": {"quality": 30},
    },
}

VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".wmv", ".flv", ".webm", ".3gp", ".m4v", ".ts", ".mts", ".vob", ".ogv", ".rmvb"}
AUDIO_EXTENSIONS = {".mp3", ".ogg", ".wav", ".flac", ".aac", ".m4a", ".opus", ".wma", ".oga", ".aiff", ".aif", ".amr"}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tiff", ".tif", ".heic", ".heif", ".gif", ".avif", ".jfif"}


def detect_type(filename):
    ext = os.path.splitext(filename)[1].lower()
    if ext == ".pdf":
        return "pdf"
    if ext == ".docx":
        return "docx"
    if ext in VIDEO_EXTENSIONS:
        return "video"
    if ext in AUDIO_EXTENSIONS:
        return "audio"
    if ext in IMAGE_EXTENSIONS:
        return "image"
    return None


def compress_audio(input_path, preset, out_path=None):
    out = out_path or (input_path + "_compressed.mp3")
    audio = AudioSegment.from_file(input_path)
    audio = audio.set_channels(AUDIO_CHANNELS).set_frame_rate(AUDIO_SAMPLE_RATE)
    audio.export(out, format=AUDIO_FORMAT, bitrate=preset["bitrate"])
    return out


def compress_video(input_path, preset, out_path=None):
    out = out_path or (input_path + "_compressed.mp4")
    cmd = (
        f'ffmpeg -y -i "{input_path}" '
        f'-vf "scale=iw:ih,format=yuv420p" -color_range 1 '
        f'-r {VIDEO_FPS} -c:v {VIDEO_CODEC} '
        f'-b:v {preset["bitrate"]} -crf {preset["crf"]} -preset {VIDEO_PRESET} '
        f'-c:a {VIDEO_AUDIO_CODEC} -b:a {VIDEO_AUDIO_BITRATE} '
        f'-ac {VIDEO_AUDIO_CHANNELS} -ar {VIDEO_AUDIO_SAMPLE_RATE} '
        f'-profile:v {VIDEO_PROFILE} -map_metadata -1 "{out}"'
    )
    subprocess.run(cmd, shell=True, check=True)
    return out


def compress_image(input_path, preset, out_path=None):
    out = out_path or (input_path + "_compressed.jpg")
    img = Image.open(input_path).convert("RGB")
    img.thumbnail(IMAGE_MAX_SIZE, Image.LANCZOS)
    img.save(out, format=IMAGE_FORMAT, quality=preset["quality"], optimize=True)
    return out


def compress_pdf(input_path, preset, out_path=None):
    out = out_path or (input_path + "_compressed.pdf")
    for gs_cmd in ["gs", "gswin64c"]:
        try:
            cmd = (
                f'{gs_cmd} -sDEVICE=pdfwrite -dCompatibilityLevel=1.4 '
                f'-dPDFSETTINGS={preset["setting"]} '
                f'-dNOPAUSE -dQUIET -dBATCH '
                f'-sOutputFile="{out}" "{input_path}"'
            )
            subprocess.run(cmd, shell=True, check=True)
            return out
        except Exception:
            continue
    raise RuntimeError("Ghostscript not found. Install gs (Linux/Mac) or gswin64c (Windows).")


def compress_docx(input_path, preset, out_path=None):
    out = out_path or (input_path + "_compressed.docx")
    doc = Document(input_path)
    for rel in doc.part.rels.values():
        if "image" in rel.reltype:
            try:
                img_part = rel.target_part
                pil_img  = Image.open(io.BytesIO(img_part.blob)).convert("RGB")
                buf = io.BytesIO()
                pil_img.save(buf, format="JPEG", quality=preset["quality"], optimize=True)
                img_part._blob = buf.getvalue()
            except Exception:
                pass
    doc.save(out)
    return out


# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/compress", methods=["POST"])
def compress():
    if "file" not in request.files:
        return jsonify({"error": "No file uploaded"}), 400

    file    = request.files["file"]
    quality = request.form.get("quality", "fast")  # "fast" or "balanced"

    if not file.filename:
        return jsonify({"error": "Empty filename"}), 400

    original_name = secure_filename(file.filename)
    file_type     = detect_type(original_name)
    if not file_type:
        return jsonify({"error": "Unsupported file type"}), 400

    if quality not in ("fast", "balanced"):
        quality = "fast"

    # Save upload using uid-only filename to avoid path issues with special chars
    uid        = uuid.uuid4().hex
    ext_in     = os.path.splitext(original_name)[1].lower()
    input_path = os.path.join(UPLOAD_FOLDER, f"{uid}_input{ext_in}")
    file.save(input_path)

    original_size = os.path.getsize(input_path)
    preset        = QUALITY_PRESETS[file_type][quality]

    # Extension for output
    EXT_OUT = {"audio": ".mp3", "video": ".mp4", "image": ".jpg", "pdf": ".pdf", "docx": ".docx"}
    out_ext  = EXT_OUT[file_type]

    # Friendly download name (kept for Content-Disposition, not used in path)
    base_name   = os.path.splitext(original_name)[0]
    download_name = base_name + "_compressed" + out_ext

    # Internal storage path uses uid only — no user filename in path
    out_path   = os.path.join(UPLOAD_FOLDER, f"{uid}_out{out_ext}")
    final_path = os.path.join(COMPRESSED_FOLDER, f"{uid}{out_ext}")

    try:
        if file_type == "audio":
            _out = compress_audio(input_path, preset, final_path)
        elif file_type == "video":
            _out = compress_video(input_path, preset, final_path)
        elif file_type == "image":
            _out = compress_image(input_path, preset, final_path)
        elif file_type == "pdf":
            _out = compress_pdf(input_path, preset, final_path)
        elif file_type == "docx":
            _out = compress_docx(input_path, preset, final_path)

        compressed_size = os.path.getsize(_out)
        saved_pct       = round(100 - (compressed_size / original_size * 100), 1)

        # _out is already at final_path, no rename needed
        os.remove(input_path)

        return jsonify({
            "success":         True,
            "file_type":       file_type,
            "original_size":   round(original_size / 1024, 1),
            "compressed_size": round(compressed_size / 1024, 1),
            "saved_pct":       saved_pct,
            "download_id":     uid + out_ext,       # clean uid-based key
            "download_name":   download_name,        # friendly name for browser
        })

    except Exception as e:
        if os.path.exists(input_path):
            os.remove(input_path)
        return jsonify({"error": str(e)}), 500


@app.route("/download/<path:filename>")
def download(filename):
    # filename = uid + ext  (e.g. "abc123.mp4")
    path = os.path.join(COMPRESSED_FOLDER, filename)
    if not os.path.exists(path):
        return jsonify({"error": "File not found or already deleted"}), 404
    # Optional friendly name passed as query param
    download_as = request.args.get("name", filename)
    return send_file(path, as_attachment=True, download_name=download_as)


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)