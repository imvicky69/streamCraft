import React from 'react';
import { Zap, ShieldCheck, Sparkles } from 'lucide-react';

export default function Features() {
  const items = [
    {
      icon: <Zap size={20} />,
      title: 'Direct Stream Download',
      desc: 'Instant streaming straight from source with zero server wait times and live speed metrics.',
    },
    {
      icon: <Sparkles size={20} />,
      title: 'Full Quality Range',
      desc: 'Choose from 360p up to 1080p Full HD MP4 or genuine 320kbps MP3 audio.',
    },
    {
      icon: <ShieldCheck size={20} />,
      title: 'Privacy Focused',
      desc: 'No logs, no trackers, and no watermarks added to your downloaded files.',
    },
  ];

  return (
    <section className="features-section" aria-label="Features and Benefits">
      <h2 className="sr-only">StreamCraft Features</h2>
      <div className="features-grid">
        {items.map((item, i) => (
          <article key={i} className="feature-card">
            <div className="feature-icon-wrapper" aria-hidden="true">{item.icon}</div>
            <h3 className="feature-title">{item.title}</h3>
            <p className="feature-desc">{item.desc}</p>
          </article>
        ))}
      </div>
    </section>
  );
}
