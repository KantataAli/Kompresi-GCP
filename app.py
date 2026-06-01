import os
import io
import uuid
import subprocess
import tempfile
from flask import Flask, request, jsonify, send_file, render_template
from flask_cors import CORS
from pydub import AudioSegment
from PIL import Image
from docx import Document
from werkzeug.utils import secure_filename
from google.cloud import storage as gcs

app = Flask(__name__)
CORS(app)

# ── GCS bucket (set via env var GCS_BUCKET, fallback to local for dev) ────────
GCS_BUCKET = os.environ.get("GCS_BUCKET", "")
USE_GCS    = bool(GCS_BUCKET)

# Local folders (used in development / fallback)
UPLOAD_FOLDER     = "/tmp/uploads"
COMPRESSED_FOLDER = "/tmp/compressed"
os.makedirs(UPLOAD_FOLDER,     exist_ok=True)
os.makedirs(COMPRESSED_FOLDER, exist_ok=True)

# ── Compression settings ───────────────────────────────────────────────────────
AUDIO_CHANNELS      = 1
AUDIO_SAMPLE_RATE   = 44100
AUDIO_FORMAT        = "mp3"

VIDEO_FPS              = 24
VIDEO_CODEC            = "libx264"
VIDEO_PRESET           = "ultrafast"
VIDEO_PROFILE          = "main"
VIDEO_AUDIO_CODEC      = "aac"
VIDEO_AUDIO_BITRATE    = "64k"
VIDEO_AUDIO_CHANNELS   = 1
VIDEO_AUDIO_SAMPLE_RATE = 44100

IMAGE_MAX_SIZE = (1920, 1080)
IMAGE_FORMAT   = "JPEG"

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
    if ext == ".pdf":   return "pdf"
    if ext == ".docx":  return "docx"
    if ext in VIDEO_EXTENSIONS: return "video"
    if ext in AUDIO_EXTENSIONS: return "audio"
    if ext in IMAGE_EXTENSIONS: return "image"
    return None


# ── GCS helpers ───────────────────────────────────────────────────────────────

def gcs_upload(local_path, blob_name):
    """Upload file to GCS and return a signed URL valid for 1 hour."""
    client  = gcs.Client()
    bucket  = client.bucket(GCS_BUCKET)
    blob    = bucket.blob(blob_name)
    blob.upload_from_filename(local_path)
    # Generate signed URL (1 hour)
    import datetime
    url = blob.generate_signed_url(
        expiration=datetime.timedelta(hours=1),
        method="GET",
        version="v4",
    )
    return url


# ── Compression functions ──────────────────────────────────────────────────────

def compress_audio(input_path, preset, out_path):
    audio = AudioSegment.from_file(input_path)
    audio = audio.set_channels(AUDIO_CHANNELS).set_frame_rate(AUDIO_SAMPLE_RATE)
    audio.export(out_path, format=AUDIO_FORMAT, bitrate=preset["bitrate"])
    return out_path


def compress_video(input_path, preset, out_path):
    cmd = (
        f'ffmpeg -y -i "{input_path}" '
        f'-vf "scale=iw:ih,format=yuv420p" -color_range 1 '
        f'-r {VIDEO_FPS} -c:v {VIDEO_CODEC} '
        f'-b:v {preset["bitrate"]} -crf {preset["crf"]} -preset {VIDEO_PRESET} '
        f'-c:a {VIDEO_AUDIO_CODEC} -b:a {VIDEO_AUDIO_BITRATE} '
        f'-ac {VIDEO_AUDIO_CHANNELS} -ar {VIDEO_AUDIO_SAMPLE_RATE} '
        f'-profile:v {VIDEO_PROFILE} -map_metadata -1 "{out_path}"'
    )
    subprocess.run(cmd, shell=True, check=True)
    return out_path


def compress_image(input_path, preset, out_path):
    img = Image.open(input_path).convert("RGB")
    img.thumbnail(IMAGE_MAX_SIZE, Image.LANCZOS)
    img.save(out_path, format=IMAGE_FORMAT, quality=preset["quality"], optimize=True)
    return out_path


def compress_pdf(input_path, preset, out_path):
    for gs_cmd in ["gs", "gswin64c"]:
        try:
            cmd = (
                f'{gs_cmd} -sDEVICE=pdfwrite -dCompatibilityLevel=1.4 '
                f'-dPDFSETTINGS={preset["setting"]} '
                f'-dNOPAUSE -dQUIET -dBATCH '
                f'-sOutputFile="{out_path}" "{input_path}"'
            )
            subprocess.run(cmd, shell=True, check=True)
            return out_path
        except Exception:
            continue
    raise RuntimeError("Ghostscript not found.")


def compress_docx(input_path, preset, out_path):
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
    doc.save(out_path)
    return out_path


# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/compress", methods=["POST"])
def compress():
    if "file" not in request.files:
        return jsonify({"error": "No file uploaded"}), 400

    file    = request.files["file"]
    quality = request.form.get("quality", "fast")

    if not file.filename:
        return jsonify({"error": "Empty filename"}), 400

    original_name = secure_filename(file.filename)
    file_type     = detect_type(original_name)
    if not file_type:
        return jsonify({"error": "Unsupported file type"}), 400

    if quality not in ("fast", "balanced"):
        quality = "fast"

    uid    = uuid.uuid4().hex
    ext_in = os.path.splitext(original_name)[1].lower()
    EXT_OUT = {"audio": ".mp3", "video": ".mp4", "image": ".jpg", "pdf": ".pdf", "docx": ".docx"}
    out_ext = EXT_OUT[file_type]

    input_path  = os.path.join(UPLOAD_FOLDER, f"{uid}_input{ext_in}")
    output_path = os.path.join(COMPRESSED_FOLDER, f"{uid}{out_ext}")
    download_name = os.path.splitext(original_name)[0] + "_compressed" + out_ext

    file.save(input_path)
    original_size = os.path.getsize(input_path)
    preset = QUALITY_PRESETS[file_type][quality]

    try:
        if file_type == "audio":   compress_audio(input_path, preset, output_path)
        elif file_type == "video": compress_video(input_path, preset, output_path)
        elif file_type == "image": compress_image(input_path, preset, output_path)
        elif file_type == "pdf":   compress_pdf(input_path, preset, output_path)
        elif file_type == "docx":  compress_docx(input_path, preset, output_path)

        compressed_size = os.path.getsize(output_path)
        saved_pct = round(100 - (compressed_size / original_size * 100), 1)
        os.remove(input_path)

        # ── GCS mode: upload dan return signed URL ─────────────────────────
        if USE_GCS:
            blob_name   = f"compressed/{uid}{out_ext}"
            signed_url  = gcs_upload(output_path, blob_name)
            os.remove(output_path)  # hapus file lokal setelah upload ke GCS
            return jsonify({
                "success":         True,
                "file_type":       file_type,
                "original_size":   round(original_size / 1024, 1),
                "compressed_size": round(compressed_size / 1024, 1),
                "saved_pct":       saved_pct,
                "download_url":    signed_url,      # langsung URL GCS
                "download_name":   download_name,
                "use_gcs":         True,
            })

        # ── Local mode: pakai route /download seperti biasa ────────────────
        return jsonify({
            "success":         True,
            "file_type":       file_type,
            "original_size":   round(original_size / 1024, 1),
            "compressed_size": round(compressed_size / 1024, 1),
            "saved_pct":       saved_pct,
            "download_id":     uid + out_ext,
            "download_name":   download_name,
            "use_gcs":         False,
        })

    except Exception as e:
        for p in [input_path, output_path]:
            if os.path.exists(p): os.remove(p)
        return jsonify({"error": str(e)}), 500


@app.route("/download/<path:filename>")
def download(filename):
    path = os.path.join(COMPRESSED_FOLDER, filename)
    if not os.path.exists(path):
        return jsonify({"error": "File not found"}), 404
    download_as = request.args.get("name", filename)
    return send_file(path, as_attachment=True, download_name=download_as)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(debug=False, host="0.0.0.0", port=port)