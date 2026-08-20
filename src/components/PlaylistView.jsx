import React, { useState } from 'react';
import { ListMusic, Archive, Music, Video, Download, Loader2, CheckCircle2, Clock } from 'lucide-react';

export default function PlaylistView({
  playlistData,
  onDownloadSingleTrack,
  onDownloadPlaylistZip,
  downloadingZip,
  zipProgress,
  zipSuccess,
}) {
  const [downloadFormat, setDownloadFormat] = useState('audio'); // 'audio' | 'video'
  const [maxTracks, setMaxTracks] = useState(10); // default 10 songs for fast download

  if (!playlistData || !playlistData.is_playlist) return null;

  const totalAvailable = playlistData.track_count || playlistData.tracks?.length || 10;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '1.25rem' }}>
      {/* Playlist Header & Batch Actions */}
      <div
        style={{
          background: 'var(--bg-card)',
          border: '1px solid var(--border-subtle)',
          borderRadius: 'var(--radius-md)',
          padding: '1.25rem',
          display: 'flex',
          flexDirection: 'column',
          gap: '1rem',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: '0.75rem' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
            <ListMusic size={24} color="var(--text-secondary)" />
            <div>
              <h3 style={{ fontSize: '1.1rem', fontWeight: 600, color: 'var(--text-primary)' }}>
                {playlistData.title}
              </h3>
              <span style={{ fontSize: '0.825rem', color: 'var(--text-muted)' }}>
                {totalAvailable} tracks detected • {playlistData.author}
              </span>
            </div>
          </div>

          <div style={{ display: 'flex', gap: '0.35rem' }}>
            <button
              type="button"
              className={`tab-button ${downloadFormat === 'audio' ? 'active' : ''}`}
              onClick={() => setDownloadFormat('audio')}
              disabled={downloadingZip}
            >
              <Music size={15} />
              <span>MP3 Audio</span>
            </button>
            <button
              type="button"
              className={`tab-button ${downloadFormat === 'video' ? 'active' : ''}`}
              onClick={() => setDownloadFormat('video')}
              disabled={downloadingZip}
            >
              <Video size={15} />
              <span>MP4 Video</span>
            </button>
          </div>
        </div>

        {/* Track Limit Selector */}
        {!downloadingZip && !zipSuccess && (
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', flexWrap: 'wrap' }}>
            <span style={{ fontSize: '0.8rem', color: 'var(--text-muted)' }}>Download limit:</span>
            {[5, 10, 25].map((num) => (
              <button
                key={num}
                type="button"
                className={`tab-button ${maxTracks === num ? 'active' : ''}`}
                style={{ padding: '0.25rem 0.65rem', fontSize: '0.775rem' }}
                onClick={() => setMaxTracks(num)}
              >
                <span>{num} Songs {num === 5 ? '(~15s)' : num === 10 ? '(~30s)' : '(~60s)'}</span>
              </button>
            ))}
          </div>
        )}

        {/* 1-Click ZIP Download Button */}
        {!downloadingZip && !zipSuccess && (
          <button
            type="button"
            className="download-cta-button"
            onClick={() =>
              onDownloadPlaylistZip({
                url: playlistData.playlist_url,
                audioOnly: downloadFormat === 'audio',
                maxTracks,
              })
            }
          >
            <Archive size={18} />
            <span>
              Download {maxTracks} Songs as ZIP ({downloadFormat === 'audio' ? 'MP3s' : 'MP4s'})
            </span>
          </button>
        )}

        {/* Live Track-by-Track ZIP Download Progress */}
        {downloadingZip && (
          <div className="progress-container">
            <div className="progress-header">
              <span className="progress-status-text" style={{ maxWidth: '80%', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                <Loader2 size={16} className="spin" />
                <span>
                  {zipProgress?.current && zipProgress?.total
                    ? `[${zipProgress.current}/${zipProgress.total}] ${zipProgress.title || 'Downloading track...'}`
                    : zipProgress?.status || 'Preparing playlist download...'}
                </span>
              </span>
              <span className="progress-percent">{zipProgress?.percent || 0}%</span>
            </div>

            <div className="progress-bar-bg">
              <div
                className="progress-bar-fill"
                style={{ width: `${Math.max(5, zipProgress?.percent || 5)}%`, transition: 'width 0.3s ease' }}
              />
            </div>

            <div className="progress-stats">
              <span style={{ display: 'flex', alignItems: 'center', gap: '0.35rem' }}>
                <Clock size={13} />
                <span>{zipProgress?.eta || 'Estimating time...'}</span>
              </span>
              <span>Converting to genuine MP3 (192 kbps)</span>
            </div>
          </div>
        )}

        {zipSuccess && (
          <div className="success-box">
            <CheckCircle2 size={18} />
            <span>
              {zipSuccess.filename
                ? `Downloaded "${zipSuccess.filename}" (${zipSuccess.size_formatted}) with ${zipSuccess.total_tracks} tracks!`
                : 'Playlist ZIP downloaded successfully to your downloads folder!'}
            </span>
          </div>
        )}
      </div>

      {/* Track Listing with direct 1-click single download */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: '0.45rem' }}>
        <span style={{ fontSize: '0.85rem', fontWeight: 600, color: 'var(--text-secondary)' }}>
          Individual Songs in Playlist ({playlistData.tracks?.length || 0}):
        </span>

        <div style={{ display: 'flex', flexDirection: 'column', gap: '0.45rem', maxHeight: '340px', overflowY: 'auto', paddingRight: '0.25rem' }}>
          {playlistData.tracks?.map((track, idx) => (
            <div
              key={track.id || idx}
              className="stream-card"
              style={{
                display: 'flex',
                flexDirection: 'row',
                alignItems: 'center',
                justifyContent: 'space-between',
                padding: '0.65rem 0.9rem',
              }}
            >
              <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', minWidth: 0 }}>
                <span style={{ fontSize: '0.8rem', fontWeight: 600, color: 'var(--text-muted)', width: '20px', textAlign: 'right' }}>
                  {idx + 1}
                </span>
                <div style={{ minWidth: 0 }}>
                  <h4
                    style={{
                      fontSize: '0.9rem',
                      fontWeight: 500,
                      color: 'var(--text-primary)',
                      whiteSpace: 'nowrap',
                      overflow: 'hidden',
                      textOverflow: 'ellipsis',
                    }}
                    title={track.title}
                  >
                    {track.title}
                  </h4>
                  <span style={{ fontSize: '0.775rem', color: 'var(--text-secondary)' }}>
                    {track.author} {track.duration_formatted ? `• ${track.duration_formatted}` : ''}
                  </span>
                </div>
              </div>

              <button
                type="button"
                style={{
                  background: 'var(--bg-surface)',
                  border: '1px solid var(--border-subtle)',
                  color: 'var(--text-primary)',
                  padding: '0.35rem 0.75rem',
                  borderRadius: 'var(--radius-sm)',
                  fontSize: '0.8rem',
                  fontWeight: 500,
                  display: 'flex',
                  alignItems: 'center',
                  gap: '0.35rem',
                  cursor: 'pointer',
                  whiteSpace: 'nowrap',
                  marginLeft: '0.75rem',
                }}
                onClick={() => onDownloadSingleTrack(track.url)}
              >
                <Download size={13} />
                <span>MP3</span>
              </button>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
