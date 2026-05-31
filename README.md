# 🗜️ CompressBot — Web Version

Chat-style file compression web app. Same logic as the Telegram bot, now running in the browser.

## Stack
- **Frontend**: Pure HTML/CSS/JS (chat UI, no framework needed)
- **Backend**: Python Flask

---

## 🚀 Setup & Run (Local)

### 1. Install system dependencies
**FFmpeg** (for video):
```bash
# Ubuntu/Debian
sudo apt install ffmpeg

# macOS
brew install ffmpeg

# Windows — download from https://ffmpeg.org/download.html
```

**Ghostscript** (for PDF):
```bash
# Ubuntu/Debian
sudo apt install ghostscript

# macOS
brew install ghostscript

# Windows — download from https://ghostscript.com/releases/gsdnld.html
```

### 2. Install Python dependencies
```bash
cd compressbot
pip install -r requirements.txt
```

### 3. Run
```bash
python app.py
```

Open your browser at **http://localhost:5000**

---

## 📁 Project Structure
```
compressbot/
├── app.py              ← Flask backend (all compression logic)
├── requirements.txt
├── templates/
│   └── index.html      ← Chat UI frontend
├── uploads/            ← Temp upload folder (auto-created)
└── compressed/         ← Compressed output folder (auto-created)
```

---

## ☁️ Deploy to GCP (Cloud Run)

### 1. Create a Dockerfile
```dockerfile
FROM python:3.11-slim

RUN apt-get update && apt-get install -y \
    ffmpeg ghostscript \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
CMD ["python", "app.py"]
```

### 2. Build & deploy
```bash
gcloud builds submit --tag gcr.io/YOUR_PROJECT/compressbot
gcloud run deploy compressbot \
  --image gcr.io/YOUR_PROJECT/compressbot \
  --platform managed \
  --allow-unauthenticated \
  --memory 1Gi
```

> **Note**: Cloud Run resets the filesystem on each request. For production,
> save compressed files to **Google Cloud Storage** and return a signed URL
> instead of `/download/<id>`.

---

## ⚙️ Compression Settings
Edit the constants at the top of `app.py` to tune quality/speed.
