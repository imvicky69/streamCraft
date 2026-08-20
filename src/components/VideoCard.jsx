import React from 'react';
import { User, Clock, Eye } from 'lucide-react';

export default function VideoCard({ videoInfo }) {
  if (!videoInfo) return null;

  return (
    <div className="video-preview-card">
      <div className="thumbnail-wrapper">
        <img
          src={videoInfo.thumbnail_url}
          alt={videoInfo.title}
          className="video-thumbnail"
          loading="lazy"
        />
        {videoInfo.length_formatted && (
          <span className="duration-badge">{videoInfo.length_formatted}</span>
        )}
      </div>

      <div className="video-details">
        <h3 className="video-title" title={videoInfo.title}>
          {videoInfo.title}
        </h3>

        <div className="video-meta-row">
          <div className="meta-item">
            <User size={15} />
            <span>{videoInfo.author}</span>
          </div>

          {videoInfo.views > 0 && (
            <div className="meta-item">
              <Eye size={15} />
              <span>{videoInfo.views.toLocaleString()} views</span>
            </div>
          )}

          <div className="meta-item">
            <Clock size={15} />
            <span>{videoInfo.length_formatted}</span>
          </div>
        </div>
      </div>
    </div>
  );
}
