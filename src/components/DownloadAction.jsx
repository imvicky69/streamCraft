import React from 'react';
import { Download, Loader2, CheckCircle2, RefreshCw, Zap, Clock, HardDrive, FileVideo, FileAudio, X } from 'lucide-react';

export default function DownloadAction({
  videoInfo,
  selectedStream,
  downloading,
  downloadProgress, // { percent, receivedMB, totalMB, speed, eta, status }
  downloadSuccess,  // boolean
  onDownload,
  onCancel,
  onReset,
}) {
  if (!videoInfo || !selectedStream) return null;

  const isAudio = !selectedStream.resolution;
  const label = isAudio
    ? `Download Audio (${selectedStream.abr || '320kbps'} - MP3)`
    : `Download Video (${selectedStream.resolution} - MP4)`;

  return (
    <div className="download-action-card">
      {!downloading && !downloadSuccess && (
        <button
          id="download-stream-btn"
          className="download-cta-button"
          onClick={onDownload}
        >
          <Download size={18} />
          <span>{label}</span>
        </button>
      )}

      {/* Enhanced Live Download Progress Dashboard */}
      {downloading && (
        <div className="live-download-dashboard">
          {/* Top Status Row */}
          <div className="download-dashboard-header">
            <div className="download-live-indicator">
              <span className="live-pulse-dot" />
              <span className="download-status-title">
                {downloadProgress?.status || 'Streaming from YouTube...'}
              </span>
            </div>

            <div className="download-header-actions">
              <span className="download-percent-pill">
                {downloadProgress?.percent > 0 ? `${downloadProgress.percent}%` : 'Connecting...'}
              </span>
              {onCancel && (
                <button
                  type="button"
                  className="cancel-download-btn"
                  onClick={onCancel}
                  title="Cancel Download"
                >
                  <X size={14} />
                  <span>Cancel</span>
                </button>
              )}
            </div>
          </div>

          {/* Sleek Progress Bar */}
          <div className="progress-bar-container">
            <div className="progress-bar-track">
              {downloadProgress?.percent > 0 ? (
                <div
                  className="progress-bar-active"
                  style={{ width: `${Math.max(3, downloadProgress.percent)}%` }}
                >
                  <div className="progress-glow-tip" />
                </div>
              ) : (
                <div className="progress-bar-active progress-indeterminate" />
              )}
            </div>
          </div>

          {/* Live Metrics Grid */}
          <div className="download-metrics-grid">
            {/* Speed Metric */}
            <div className="metric-box">
              <div className="metric-label">
                <Zap size={13} className="metric-icon speed-icon" />
                <span>Speed</span>
              </div>
              <div className="metric-value">
                {downloadProgress?.speed || 'Measuring...'}
              </div>
            </div>

            {/* Transferred Size Metric */}
            <div className="metric-box">
              <div className="metric-label">
                <HardDrive size={13} className="metric-icon size-icon" />
                <span>Transferred</span>
              </div>
              <div className="metric-value">
                {downloadProgress?.receivedMB
                  ? `${downloadProgress.receivedMB} MB ${
                      downloadProgress.totalMB ? `/ ${downloadProgress.totalMB} MB` : ''
                    }`
                  : '0.0 MB'}
              </div>
            </div>

            {/* Time Remaining Metric */}
            <div className="metric-box">
              <div className="metric-label">
                <Clock size={13} className="metric-icon time-icon" />
                <span>ETA</span>
              </div>
              <div className="metric-value">
                {downloadProgress?.eta || 'Calculating...'}
              </div>
            </div>

            {/* Quality / Format Metric */}
            <div className="metric-box">
              <div className="metric-label">
                {isAudio ? (
                  <FileAudio size={13} className="metric-icon format-icon" />
                ) : (
                  <FileVideo size={13} className="metric-icon format-icon" />
                )}
                <span>Format</span>
              </div>
              <div className="metric-value">
                {isAudio ? `${selectedStream.abr || '320k'} MP3` : `${selectedStream.resolution} MP4`}
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Success State */}
      {downloadSuccess && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
          <div className="success-box">
            <CheckCircle2 size={20} />
            <div>
              <div style={{ fontWeight: 600 }}>Download Completed!</div>
              <div style={{ fontSize: '0.8rem', opacity: 0.9 }}>
                File has been saved directly to your browser's Downloads folder.
              </div>
            </div>
          </div>

          <button
            type="button"
            className="download-cta-button"
            style={{
              background: 'var(--bg-card)',
              color: 'var(--text-primary)',
              border: '1px solid var(--border-subtle)',
            }}
            onClick={onReset}
          >
            <RefreshCw size={16} />
            <span>Download Another Video</span>
          </button>
        </div>
      )}
    </div>
  );
}
