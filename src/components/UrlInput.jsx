import React from 'react';
import { Link, Loader2, ArrowRight, Clipboard } from 'lucide-react';

export default function UrlInput({ url, setUrl, onFetch, loading }) {
  const handlePaste = async () => {
    try {
      const text = await navigator.clipboard.readText();
      if (text) {
        setUrl(text);
      }
    } catch (err) {
      console.warn('Clipboard access denied or not supported');
    }
  };

  const handleKeyDown = (e) => {
    if (e.key === 'Enter' && url.trim() && !loading) {
      onFetch();
    }
  };

  return (
    <div className="input-group">
      <div className="url-input-wrapper">
        <Link className="url-input-icon" size={20} />
        <input
          id="youtube-url-input"
          type="text"
          className="url-input"
          placeholder="Paste YouTube Video or Shorts URL (e.g. https://youtu.be/...)"
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          onKeyDown={handleKeyDown}
          disabled={loading}
          autoComplete="off"
        />
        {!url && (
          <button
            type="button"
            className="paste-button"
            onClick={handlePaste}
            title="Paste from clipboard"
          >
            Paste
          </button>
        )}
      </div>

      <button
        id="fetch-video-btn"
        className="fetch-button"
        onClick={onFetch}
        disabled={loading || !url.trim()}
      >
        {loading ? (
          <>
            <Loader2 className="spin" size={18} />
            <span>Analyzing...</span>
          </>
        ) : (
          <>
            <span>Get Info</span>
            <ArrowRight size={18} />
          </>
        )}
      </button>
    </div>
  );
}
