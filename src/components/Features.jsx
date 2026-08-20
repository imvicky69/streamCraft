import React from 'react';
import { Zap, ShieldCheck, Sparkles, Layers } from 'lucide-react';

export default function Features() {
  const items = [
    {
      icon: <Zap size={20} />,
      title: 'Direct Stream Download',
      desc: 'Instant streaming straight from source with zero server wait times.',
    },
    {
      icon: <Sparkles size={20} />,
      title: 'Full Quality Range',
      desc: 'Choose from 360p up to 1080p Full HD MP4 or crystal-clear audio.',
    },
    {
      icon: <ShieldCheck size={20} />,
      title: 'Privacy Focused',
      desc: 'No logs, no trackers, and no watermarks added to your downloaded media.',
    },
  ];

  return (
    <div className="features-grid">
      {items.map((item, i) => (
        <div key={i} className="feature-card">
          <div className="feature-icon-wrapper">{item.icon}</div>
          <h4 className="feature-title">{item.title}</h4>
          <p className="feature-desc">{item.desc}</p>
        </div>
      ))}
    </div>
  );
}
