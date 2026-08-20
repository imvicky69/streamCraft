# 🚀 StreamCraft — YouTube Video & Audio Downloader

A modern, responsive full-stack web application for downloading YouTube videos and audio with instant metadata extraction, quality/format selection (1080p, 720p, 480p, 360p, MP3/M4A), and fast streaming downloads.

Built with **React + Vite** frontend and **Python FastAPI (pytubefix)** serverless backend, configured for both **local development** and **1-click Vercel deployment** with zero external servers required.

---

## ✨ Features

- **Step-by-Step Flow**:
  1. **Paste URL**: Input box with 1-click clipboard paste and instant analysis.
  2. **Live Preview**: HD video thumbnail, author, duration, and view count.
  3. **Quality & Format Selector**: Clean tabs for Video (MP4) and Audio (M4A/MP3) with estimated file sizes.
  4. **Direct Download**: 1-click streaming download directly to your browser.
- **Glassmorphic UI**: Deep dark-mode aesthetic with vibrant gradients, glowing accents, and smooth micro-animations.
- **Vercel Serverless Ready**: Python backend is in `/api/index.py` with `vercel.json` routing rules for seamless cloud hosting.

---

## 📁 Project Structure

```
youtube-downloader-app/
├── api/
│   ├── index.py              # FastAPI backend & Vercel Serverless Function
│   └── requirements.txt      # Python dependencies for Vercel
├── src/
│   ├── components/
│   │   ├── StepIndicator.jsx # 1-2-3 progress step indicator
│   │   ├── UrlInput.jsx      # URL input with clipboard paste
│   │   ├── VideoCard.jsx     # Video thumbnail & metadata preview
│   │   ├── FormatSelector.jsx# Resolution & audio bitrate picker
│   │   ├── DownloadAction.jsx# Download trigger button
│   │   └── Features.jsx      # Highlights & features
│   ├── App.jsx               # Main state orchestrator
│   ├── index.css             # Glassmorphic CSS design system
│   └── main.jsx
├── dist/                     # Production build output
├── vercel.json               # Vercel deployment rewrites
├── vite.config.js            # Vite configuration & /api proxy
├── package.json              # NPM scripts and dependencies
└── requirements.txt          # Python dependencies
```

---

## 🛠 Local Development

### 1. Start Both Backend & Frontend (Single Command)
```bash
npm start
```
This runs:
- **FastAPI backend** on `http://127.0.0.1:8000`
- **Vite frontend** on `http://localhost:5173` (with `/api` proxy)

Open **http://localhost:5173** in your browser.

---

## ☁️ Deploy to Vercel (No Localhost Required)

### Method 1: Deploy via GitHub (Recommended)
1. Push this folder to a GitHub repository:
   ```bash
   git init
   git add .
   git commit -m "Initial commit"
   git remote add origin https://github.com/your-username/youtube-downloader-app.git
   git push -u origin main
   ```
2. Go to [vercel.com](https://vercel.com) and click **"Add New Project"**.
3. Import your GitHub repository.
4. Framework Preset will auto-detect as **Vite**.
5. Click **Deploy**. Vercel will automatically build the React frontend and deploy the Python backend in `/api`.

### Method 2: Deploy with Vercel CLI
```bash
npx vercel
```
Follow the prompts, and your app will be live on `https://your-app.vercel.app`.
