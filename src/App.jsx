import React, { useState, useEffect } from 'react';
import { AlertCircle, DownloadCloud, Sun, Moon } from 'lucide-react';
import StepIndicator from './components/StepIndicator';
import UrlInput from './components/UrlInput';
import VideoCard from './components/VideoCard';
import FormatSelector from './components/FormatSelector';
import DownloadAction from './components/DownloadAction';
import PlaylistView from './components/PlaylistView';
import Features from './components/Features';
import Scanner from './components/Scanner';

export default function App() {
  const [theme, setTheme] = useState(() => {
    return localStorage.getItem('theme') || 'dark';
  });

  const [url, setUrl] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [videoInfo, setVideoInfo] = useState(null);
  const [playlistData, setPlaylistData] = useState(null);
  const [selectedStream, setSelectedStream] = useState(null);
  
  // Single Video Download State
  const [downloading, setDownloading] = useState(false);
  const [downloadProgress, setDownloadProgress] = useState({ percent: 0, receivedMB: '0', totalMB: '', status: '' });
  const [downloadSuccess, setDownloadSuccess] = useState(false);

  // Playlist Zip Download State
  const [downloadingZip, setDownloadingZip] = useState(false);
  const [zipProgress, setZipProgress] = useState({ percent: 0, current: 0, total: 0, title: '', eta: '', status: '' });
  const [zipSuccess, setZipSuccess] = useState(null);

  // Sync theme with html data-theme attribute & localStorage
  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme);
    localStorage.setItem('theme', theme);
  }, [theme]);

  const toggleTheme = () => {
    setTheme(prev => (prev === 'dark' ? 'light' : 'dark'));
  };

  // Calculate current step
  const currentStep = (videoInfo || playlistData) ? (downloadSuccess || zipSuccess ? 3 : 2) : 1;

  const handleFetchInfo = async (overrideUrl) => {
    const targetUrl = overrideUrl || url;
    let cleanInput = targetUrl.trim().replace(/\s+/g, '');
    if (!cleanInput) return;

    if (!cleanInput.startsWith('http://') && !cleanInput.startsWith('https://')) {
      cleanInput = 'https://' + cleanInput;
    }

    // Strip radio mix / playlist params from single watch URLs
    if (cleanInput.includes('watch?v=') && cleanInput.includes('&list=')) {
      cleanInput = cleanInput.split('&list=')[0];
    }
    if (cleanInput.includes('watch?v=') && cleanInput.includes('&index=')) {
      cleanInput = cleanInput.split('&index=')[0];
    }
    cleanInput = cleanInput.replace(/[&?]+$/, '');

    setUrl(cleanInput);

    setLoading(true);
    setError('');
    setVideoInfo(null);
    setPlaylistData(null);
    setSelectedStream(null);
    setDownloadSuccess(false);
    setZipSuccess(null);

    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 60000);

    try {
      const res = await fetch('/api/info', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ url: cleanInput }),
        signal: controller.signal,
      });

      clearTimeout(timeoutId);

      if (!res.ok) {
        if (res.status === 502 || res.status === 504) {
          throw new Error('Backend server is not running on port 8000. Start it with "npm start" or "npm run backend".');
        }
        let errorMsg = 'Failed to fetch details.';
        try {
          const errData = await res.json();
          errorMsg = errData.detail || errorMsg;
        } catch {
          errorMsg = `Server error (${res.status} ${res.statusText})`;
        }
        throw new Error(errorMsg);
      }

      const data = await res.json();

      if (data.is_playlist) {
        setPlaylistData(data);
      } else {
        setVideoInfo(data);
        if (data.video_streams && data.video_streams.length > 0) {
          setSelectedStream(data.video_streams[0]);
        } else if (data.audio_streams && data.audio_streams.length > 0) {
          setSelectedStream(data.audio_streams[0]);
        }
      }
    } catch (err) {
      clearTimeout(timeoutId);
      console.error('Fetch error:', err);
      if (err.name === 'AbortError') {
        setError('Request timed out while analyzing. Please check the URL and try again.');
      } else {
        setError(err.message || 'Network error while contacting server.');
      }
    } finally {
      setLoading(false);
    }
  };

  const handleSelectStream = (stream) => {
    setSelectedStream(stream);
    setDownloadSuccess(false);
  };

  const [downloadController, setDownloadController] = useState(null);

  const handleCancelDownload = () => {
    if (downloadController) {
      downloadController.abort();
      setDownloadController(null);
    }
    setDownloading(false);
    setDownloadProgress({ percent: 0, receivedMB: '0', totalMB: '', speed: '', eta: '', status: '' });
  };

  const handleDownload = async () => {
    if (!videoInfo || !selectedStream) return;

    const controller = new AbortController();
    setDownloadController(controller);
    setDownloading(true);
    setDownloadSuccess(false);
    setDownloadProgress({
      percent: 0,
      receivedMB: '0',
      totalMB: '',
      speed: 'Connecting...',
      eta: 'Calculating...',
      status: 'Connecting to YouTube stream...',
    });
    setError('');

    try {
      const isAudio = !selectedStream.resolution;
      const downloadUrl = `/api/download?url=${encodeURIComponent(url.trim())}&itag=${selectedStream.itag}&audio_only=${isAudio}`;

      const response = await fetch(downloadUrl, { signal: controller.signal });
      if (!response.ok) {
        let errText = 'Download failed.';
        try {
          const errJson = await response.json();
          errText = errJson.detail || errText;
        } catch {
          errText = `Download failed with status ${response.status}`;
        }
        throw new Error(errText);
      }

      const contentLength = response.headers.get('Content-Length');
      const totalBytes = contentLength ? parseInt(contentLength, 10) : (selectedStream.filesize || 0);
      const mbTotal = totalBytes > 0 ? (totalBytes / (1024 * 1024)).toFixed(1) : '';

      let receivedBytes = 0;
      const reader = response.body.getReader();
      const chunks = [];

      let startTime = performance.now();
      let lastSpeedCalcTime = startTime;
      let lastSpeedBytes = 0;
      let currentSpeed = '0 MB/s';
      let currentEta = 'Calculating...';

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        chunks.push(value);
        receivedBytes += value.length;

        const now = performance.now();
        const timeDiff = (now - lastSpeedCalcTime) / 1000;

        // Update speed and ETA every 350ms
        if (timeDiff >= 0.35) {
          const bytesDiff = receivedBytes - lastSpeedBytes;
          const bytesPerSec = bytesDiff / timeDiff;

          if (bytesPerSec >= 1024 * 1024) {
            currentSpeed = `${(bytesPerSec / (1024 * 1024)).toFixed(1)} MB/s`;
          } else {
            currentSpeed = `${(bytesPerSec / 1024).toFixed(0)} KB/s`;
          }

          if (totalBytes > 0 && bytesPerSec > 0) {
            const remainingBytes = Math.max(0, totalBytes - receivedBytes);
            const remainingSec = Math.ceil(remainingBytes / bytesPerSec);
            if (remainingSec >= 60) {
              currentEta = `~${Math.floor(remainingSec / 60)}m ${remainingSec % 60}s`;
            } else {
              currentEta = `~${remainingSec}s left`;
            }
          }

          lastSpeedCalcTime = now;
          lastSpeedBytes = receivedBytes;
        }

        const mbReceived = (receivedBytes / (1024 * 1024)).toFixed(1);
        const percent = totalBytes > 0 ? Math.min(100, Math.round((receivedBytes / totalBytes) * 100)) : 0;

        setDownloadProgress({
          percent,
          receivedMB: mbReceived,
          totalMB: mbTotal,
          speed: currentSpeed,
          eta: currentEta,
          status: isAudio ? 'Converting & Downloading genuine MP3...' : 'Downloading Video Stream...',
        });
      }

      // Create Blob and trigger file download
      const blob = new Blob(chunks, {
        type: isAudio ? 'audio/mpeg' : (selectedStream.mime_type || 'video/mp4'),
      });
      const blobUrl = window.URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = blobUrl;
      const ext = isAudio ? 'mp3' : (selectedStream.extension || 'mp4');
      const cleanTitle = (videoInfo.title || 'video').replace(/[\\/*?:"<>|]/g, '');
      a.download = `${cleanTitle}.${ext}`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      window.URL.revokeObjectURL(blobUrl);

      setDownloadSuccess(true);
    } catch (err) {
      if (err.name === 'AbortError') {
        console.log('Download cancelled by user.');
      } else {
        console.error('Download error:', err);
        setError(err.message || 'Could not complete download.');
      }
    } finally {
      setDownloading(false);
      setDownloadController(null);
    }
  };

  // Real-time SSE playlist ZIP download with live ETA & progress
  const handleDownloadPlaylistZip = ({ url, audioOnly, maxTracks }) => {
    setDownloadingZip(true);
    setZipSuccess(null);
    setZipProgress({ percent: 0, current: 0, total: 0, title: '', eta: 'Starting download...', status: 'Connecting...' });
    setError('');

    const sseUrl = `/api/playlist-zip-sse?url=${encodeURIComponent(url.trim())}&audio_only=${audioOnly}&max_tracks=${maxTracks}`;
    const eventSource = new EventSource(sseUrl);

    eventSource.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        if (data.type === 'start') {
          setZipProgress({
            percent: 5,
            current: 0,
            total: data.total,
            title: 'Starting tracks...',
            eta: 'Calculating...',
            status: `Starting ${data.total} tracks...`,
          });
        } else if (data.type === 'progress') {
          setZipProgress({
            percent: data.percent,
            current: data.current,
            total: data.total,
            title: data.title,
            eta: data.eta,
            status: `Downloading ${data.current}/${data.total}: "${data.title}"`,
          });
        } else if (data.type === 'zipping') {
          setZipProgress({
            percent: data.percent,
            title: 'Zipping...',
            eta: 'Almost done...',
            status: 'Packaging all tracks into ZIP file...',
          });
        } else if (data.type === 'complete') {
          eventSource.close();
          setDownloadingZip(false);
          setZipSuccess(data);

          // Auto-trigger browser download of the ready ZIP file
          const a = document.createElement('a');
          a.href = `/api/get-zip-file?file_id=${data.file_id}`;
          a.download = data.filename || 'playlist.zip';
          document.body.appendChild(a);
          a.click();
          document.body.removeChild(a);
        } else if (data.type === 'error') {
          eventSource.close();
          setDownloadingZip(false);
          setError(data.message || 'Error occurred during playlist download.');
        }
      } catch (err) {
        console.error('SSE parse error:', err);
      }
    };

    eventSource.onerror = (err) => {
      console.error('SSE connection error:', err);
      eventSource.close();
      setDownloadingZip(false);
      setError('Connection to download server interrupted. Please try again.');
    };
  };

  const handleReset = () => {
    setUrl('');
    setVideoInfo(null);
    setPlaylistData(null);
    setSelectedStream(null);
    setDownloadSuccess(false);
    setZipSuccess(null);
    setError('');
  };

  return (
    <>
      {/* React Bits Scanner Animated Background */}
      <div className="background-scanner-wrapper">
        <Scanner
          color1={theme === 'dark' ? '#38bdf8' : '#0284c7'}
          color2={theme === 'dark' ? '#818cf8' : '#6366f1'}
          color3={theme === 'dark' ? '#e2e8f0' : '#0369a1'}
          opacity={theme === 'dark' ? 0.45 : 0.75}
          brightness={theme === 'dark' ? 1.05 : 1.25}
          speed={0.35}
          sweepSpeed={0.25}
          bandDensity={1.1}
          lineSharpness={0.8}
          glow={theme === 'dark' ? 0.45 : 0.7}
          softness={0.7}
          scale={1.1}
          scanDirection="diagonal"
          mouseInteraction={true}
        />
      </div>

      {/* Top Glassmorphic Navigation Bar */}
      <nav className="main-navbar">
        <div className="navbar-inner">
          <div className="nav-brand" onClick={handleReset}>
            <img src="/logo-clear.png" alt="StreamCraft" className="nav-logo-raw" />
            <span className="nav-brand-title">StreamCraft</span>
            <span className="nav-version-badge">v1.0</span>
          </div>

          <div className="nav-actions">
            <div className="engine-status-pill">
              <span className="status-dot-green" />
              <span>Fast HD</span>
            </div>

            <button
              type="button"
              className="theme-toggle-btn"
              onClick={toggleTheme}
              title={`Switch to ${theme === 'dark' ? 'Light' : 'Dark'} theme`}
              id="theme-toggle"
            >
              {theme === 'dark' ? <Sun size={14} /> : <Moon size={14} />}
              <span>{theme === 'dark' ? 'Light' : 'Dark'}</span>
            </button>
          </div>
        </div>
      </nav>

      <div className="app-container">
        {/* Header with Clear Raw Hero Logo */}
        <header className="app-header">
          <div className="hero-logo-wrapper">
            <img src="/logo-clear.png" alt="StreamCraft" className="hero-logo-raw" />
          </div>

          <h1 className="app-title">YouTube & YouTube Music Downloader</h1>

          <p className="app-subtitle">
            Download high-quality videos (1080p, 720p, 480p), MP3 audio, or full playlists as a ZIP directly in your browser.
          </p>
        </header>

        {/* Step Tracker */}
        <StepIndicator currentStep={currentStep} />

        {/* Main Clean Card */}
        <main className="clean-card">
          {/* Step 1: URL Input */}
          <UrlInput
            url={url}
            setUrl={setUrl}
            onFetch={() => handleFetchInfo()}
            loading={loading}
          />

          {/* Error Notification */}
          {error && (
            <div className="error-banner">
              <AlertCircle size={18} />
              <span>{error}</span>
            </div>
          )}

          {/* Playlist View with Live Track-by-Track ZIP Download */}
          {playlistData && (
            <PlaylistView
              playlistData={playlistData}
              onSelectTrack={(trackUrl) => handleFetchInfo(trackUrl)}
              onDownloadSingleTrack={(trackUrl) => handleFetchInfo(trackUrl)}
              onDownloadPlaylistZip={handleDownloadPlaylistZip}
              downloadingZip={downloadingZip}
              zipProgress={zipProgress}
              zipSuccess={zipSuccess}
            />
          )}

          {/* Step 2: Video Card Preview */}
          {videoInfo && <VideoCard videoInfo={videoInfo} />}

          {/* Step 3: Quality & Format Selector */}
          {videoInfo && (
            <FormatSelector
              videoInfo={videoInfo}
              selectedStream={selectedStream}
              onSelectStream={handleSelectStream}
            />
          )}

          {/* Step 4: Download Action & Live Progress */}
          {videoInfo && selectedStream && (
            <DownloadAction
              videoInfo={videoInfo}
              selectedStream={selectedStream}
              downloading={downloading}
              downloadProgress={downloadProgress}
              downloadSuccess={downloadSuccess}
              onDownload={handleDownload}
              onCancel={handleCancelDownload}
              onReset={handleReset}
            />
          )}
        </main>

        {/* Minimal Features */}
        <Features />

        {/* Footer */}
        <footer className="app-footer">
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '0.5rem' }}>
            <img src="/logo-clear.png" alt="StreamCraft" className="footer-logo-raw" />
            <span style={{ fontWeight: 600, color: 'var(--text-primary)' }}>StreamCraft</span>
            <span>• Fast HD Video & Audio Downloader</span>
          </div>
          <p style={{ opacity: 0.7, fontSize: '0.75rem' }}>FastAPI + React Vite • Ready for Vercel Deployment</p>
        </footer>
      </div>
    </>
  );
}
