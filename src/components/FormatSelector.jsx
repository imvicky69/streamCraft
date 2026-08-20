import React, { useState } from 'react';
import { Video, Music, CheckCircle2 } from 'lucide-react';

export default function FormatSelector({ videoInfo, selectedStream, onSelectStream }) {
  const [activeTab, setActiveTab] = useState('video'); // 'video' | 'audio'

  const videoStreams = videoInfo?.video_streams || [];
  const audioStreams = videoInfo?.audio_streams || [];

  return (
    <div className="format-section">
      <div className="format-tabs">
        <button
          type="button"
          className={`tab-button ${activeTab === 'video' ? 'active' : ''}`}
          onClick={() => setActiveTab('video')}
        >
          <Video size={16} />
          <span>Video ({videoStreams.length})</span>
        </button>

        <button
          type="button"
          className={`tab-button ${activeTab === 'audio' ? 'active' : ''}`}
          onClick={() => setActiveTab('audio')}
        >
          <Music size={16} />
          <span>Audio ({audioStreams.length})</span>
        </button>
      </div>

      <div className="streams-grid">
        {activeTab === 'video' ? (
          videoStreams.map((stream) => {
            const isSelected = selectedStream?.itag === stream.itag;

            return (
              <div
                key={stream.itag}
                className={`stream-card ${isSelected ? 'selected' : ''}`}
                onClick={() => onSelectStream(stream, 'video')}
              >
                <div className="stream-header">
                  <span className="quality-badge">{stream.resolution}</span>
                  <span className="stream-ext">{stream.extension}</span>
                </div>

                <span className="stream-meta">
                  {stream.has_audio ? 'Full Video + Audio' : 'High-Def Video'} ({stream.fps}fps)
                </span>

                <span className="stream-size">
                  {stream.filesize_formatted}
                </span>

                {isSelected && (
                  <CheckCircle2
                    size={16}
                    color="#6366f1"
                    style={{ position: 'absolute', top: 10, right: 10 }}
                  />
                )}
              </div>
            );
          })
        ) : (
          audioStreams.map((stream) => {
            const isSelected = selectedStream?.itag === stream.itag;

            return (
              <div
                key={stream.itag}
                className={`stream-card ${isSelected ? 'selected' : ''}`}
                onClick={() => onSelectStream(stream, 'audio')}
              >
                <div className="stream-header">
                  <span className="quality-badge">{stream.abr}</span>
                  <span className="stream-ext">{stream.extension}</span>
                </div>

                <span className="stream-meta">High Quality Audio Stream</span>

                <span className="stream-size">
                  {stream.filesize_formatted}
                </span>

                {isSelected && (
                  <CheckCircle2
                    size={16}
                    color="#6366f1"
                    style={{ position: 'absolute', top: 10, right: 10 }}
                  />
                )}
              </div>
            );
          })
        )}
      </div>
    </div>
  );
}
