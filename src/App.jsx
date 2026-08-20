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

const BACKEND_BASE =
  import.meta.env.VITE_API_URL ||
  (typeof window !== 'undefined' &&
  (window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1')
    ? ''
    : 'https://streamcraft-backend.onrender.com');

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
    let cleanInput = targetUrl.trim();
    if (!cleanInput) return;

    const isExplicitUrl = cleanInput.startsWith('http://') || 
                          cleanInput.startsWith('https://') || 
                          cleanInput.includes('youtube.com') || 
                          cleanInput.includes('youtu.be');

    if (isExplicitUrl) {
      cleanInput = cleanInput.replace(/\s+/g, '');
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
    }

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
      const res = await fetch(`${BACKEND_BASE}/api/info`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ url: cleanInput }),
        signal: controller.signal,
      });

      clearTimeout(timeoutId);

      if (!res.ok) {
        if (res.status === 502 || res.status === 504) {
          throw new Error('Backend server is not running or starting up on Render. Please wait 30s and try again.');
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
  const [currentDownloadId, setCurrentDownloadId] = useState(null);

  const handleCancelDownload = () => {
    if (downloadController) {
      downloadController.abort();
      setDownloadController(null);
    }
    if (currentDownloadId) {
      fetch(`${BACKEND_BASE}/api/cancel-download?download_id=${currentDownloadId}`, { method: 'POST' }).catch(() => {});
      setCurrentDownloadId(null);
    }
    setDownloading(false);
    setDownloadProgress({ percent: 0, receivedMB: '0', totalMB: '', speed: '', eta: '', status: '' });
  };

  const handleDownload = async () => {
    if (!videoInfo || !selectedStream) return;

    const isAudio = !selectedStream.resolution;

    // 🚀 ULTRA-FAST DIRECT STREAM PATH:
    if (!isAudio && selectedStream.direct_url) {
      setDownloading(true);
      setDownloadSuccess(false);
      setDownloadProgress({
        percent: 100,
        receivedMB: selectedStream.filesize_formatted || '',
        totalMB: selectedStream.filesize_formatted || '',
        speed: 'Max Speed',
        eta: 'Instant',
        status: '🚀 Streaming directly from YouTube CDN at maximum network speed...',
      });

      const cleanTitle = (videoInfo.title || 'video').replace(/[\\/*?:"<>|]/g, '');
      const pipeUrl = `${BACKEND_BASE}/api/proxy-pipe?stream_url=${encodeURIComponent(selectedStream.direct_url)}&title=${encodeURIComponent(cleanTitle)}&ext=${selectedStream.extension || 'mp4'}`;

      const a = document.createElement('a');
      a.href = pipeUrl;
      a.download = `${cleanTitle}.${selectedStream.extension || 'mp4'}`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);

      setTimeout(() => {
        setDownloadSuccess(true);
        setDownloading(false);
      }, 1200);
      return;
    }

    const downloadId = 'dl_' + Date.now() + '_' + Math.random().toString(36).substring(2, 8);
    setCurrentDownloadId(downloadId);

    const controller = new AbortController();
    setDownloadController(controller);
    setDownloading(true);
    setDownloadSuccess(false);
    setDownloadProgress({
      percent: 5,
      receivedMB: '0',
      totalMB: selectedStream.filesize ? (selectedStream.filesize / (1024 * 1024)).toFixed(1) : '',
      speed: 'Connecting...',
      eta: 'Calculating...',
      status: '⚡ Connecting to YouTube stream...',
    });
    setError('');

    try {
      const downloadSseUrl = `${BACKEND_BASE}/api/download-single-sse?url=${encodeURIComponent(url.trim())}&itag=${selectedStream.itag}&audio_only=${isAudio}&download_id=${downloadId}`;

      const response = await fetch(downloadSseUrl, { signal: controller.signal });
      if (!response.ok) {
        if (response.status === 502 || response.status === 504) {
          console.warn('Vercel 502 proxy detected on SSE. Switching to direct download mode...');
          setDownloadProgress(prev => ({
            ...prev,
            percent: 90,
            status: '⚡ Downloading directly from server...',
          }));

          const directUrl = `${BACKEND_BASE}/api/download-direct?url=${encodeURIComponent(url.trim())}&itag=${selectedStream.itag}&audio_only=${isAudio}`;
          const cleanTitle = (videoInfo.title || 'media').replace(/[\\/*?:"<>|]/g, '');
          const a = document.createElement('a');
          a.href = directUrl;
          a.download = `${cleanTitle}.${isAudio ? 'mp3' : 'mp4'}`;
          document.body.appendChild(a);
          a.click();
          document.body.removeChild(a);

          setDownloadSuccess(true);
          setDownloading(false);
          return;
        }

        let errText = 'Download failed to initialize.';
        try {
          const errJson = await response.json();
          errText = errJson.detail || errText;
        } catch {
          errText = `Download failed with status ${response.status}`;
        }
        throw new Error(errText);
      }

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n\n');
        buffer = lines.pop() || '';

        for (const line of lines) {
          const trimmed = line.trim();
          if (!trimmed.startsWith('data:')) continue;
          const jsonStr = trimmed.replace(/^data:\s*/, '');
          try {
            const data = JSON.parse(jsonStr);

            if (data.type === 'progress') {
              setDownloadProgress(prev => ({
                ...prev,
                percent: data.percent || prev.percent,
                receivedMB: data.receivedMB || prev.receivedMB,
                totalMB: data.totalMB || prev.totalMB,
                speed: data.speed || prev.speed,
                eta: data.eta || prev.eta,
                status: data.status || prev.status,
              }));
            } else if (data.type === 'converting') {
              setDownloadProgress(prev => ({
                ...prev,
                percent: data.percent || 92,
                speed: data.speed || 'Processing',
                eta: data.eta || 'Few seconds',
                status: data.status || '✨ Converting / Packaging stream...',
              }));
            } else if (data.type === 'complete') {
              setDownloadProgress({
                percent: 100,
                receivedMB: data.size_formatted || '',
                totalMB: data.size_formatted || '',
                speed: 'Finished',
                eta: '0s',
                status: '🎉 Download complete! Starting browser download...',
              });

              // Trigger native browser download directly from Render backend
              const fileUrl = `${BACKEND_BASE}/api/get-single-file?file_id=${data.file_id}`;
              const a = document.createElement('a');
              a.href = fileUrl;
              a.download = data.filename || 'media';
              document.body.appendChild(a);
              a.click();
              document.body.removeChild(a);

              setDownloadSuccess(true);
              setDownloading(false);
              return;
            } else if (data.type === 'error') {
              throw new Error(data.message || 'Download failed on server.');
            }
          } catch (pe) {
            if (pe.message && !pe.message.includes('JSON')) {
              throw pe;
            }
          }
        }
      }
    } catch (err) {
      if (err.name === 'AbortError') {
        console.log('Download cancelled by user.');
      } else {
        console.error('Download error:', err);
        setError(err.message || 'Could not complete download.');
      }
      setDownloading(false);
    } finally {
      setDownloadController(null);
    }
  };

  // Real-time SSE playlist ZIP download with live ETA & progress
  const handleDownloadPlaylistZip = ({ url, audioOnly, maxTracks }) => {
    setDownloadingZip(true);
    setZipSuccess(null);
    setZipProgress({ percent: 0, current: 0, total: 0, title: '', eta: 'Starting download...', status: 'Connecting...' });
    setError('');

    const sseUrl = `${BACKEND_BASE}/api/playlist-zip-sse?url=${encodeURIComponent(url.trim())}&audio_only=${audioOnly}&max_tracks=${maxTracks}`;
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

          // Auto-trigger browser download of the ready ZIP file directly from Render backend
          const a = document.createElement('a');
          a.href = `${BACKEND_BASE}/api/get-zip-file?file_id=${data.file_id}`;
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
