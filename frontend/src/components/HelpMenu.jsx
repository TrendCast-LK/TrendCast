import { useEffect, useRef, useState } from "react";

const FAQS = [
  {
    q: "How do predictions work?",
    a: "Go to New Prediction, describe the video (title, category, tags, optional thumbnail), and submit. You'll get a 7-day view forecast, a confidence score, and how it compares to your channel average.",
  },
  {
    q: "Why does my Channel page show an error?",
    a: "The server needs a YouTube Data API key configured to fetch real channel stats. Ask whoever runs this deployment to set YOUTUBE_API_KEY, then hit Refresh on the Channel page.",
  },
  {
    q: "What's the difference between Trends and a single Prediction?",
    a: "A Prediction Result shows the forecast for one video. Trends aggregates across all of your predictions — averages, best-performing category, and a timeline.",
  },
  {
    q: "Can I change my subscriber/view numbers?",
    a: "Yes — Settings → Profile. Those numbers are the baseline the forecast engine uses, so keeping them accurate improves predictions.",
  },
];

export default function HelpMenu() {
  const [open, setOpen] = useState(false);
  const containerRef = useRef(null);

  useEffect(() => {
    function handleClickOutside(e) {
      if (containerRef.current && !containerRef.current.contains(e.target)) {
        setOpen(false);
      }
    }
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  return (
    <div className="relative" ref={containerRef}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="p-2 text-on-surface-variant hover:bg-surface-container-high/50 rounded-full transition-colors active:scale-95"
        aria-label="Help"
      >
        <span className="material-symbols-outlined">help</span>
      </button>

      {open && (
        <div className="absolute right-0 mt-2 w-96 max-h-[28rem] overflow-y-auto glass-panel rounded-xl shadow-[0_20px_40px_rgba(0,0,0,0.15)] z-50">
          <div className="px-4 py-3 border-b border-outline-variant/30 sticky top-0 bg-surface-container-lowest/90 backdrop-blur">
            <span className="font-headline-md text-headline-md text-on-background">Help &amp; Support</span>
          </div>

          <div className="p-4 flex flex-col gap-4">
            {FAQS.map((item) => (
              <div key={item.q}>
                <p className="font-label-md text-label-md text-on-background mb-1">{item.q}</p>
                <p className="font-body-md text-body-md text-on-surface-variant text-sm">{item.a}</p>
              </div>
            ))}
          </div>

          <div className="px-4 py-3 border-t border-outline-variant/30">
            <a
              href="mailto:support@viewcast.example"
              className="font-label-md text-label-md text-primary hover:text-secondary transition-colors flex items-center gap-2"
            >
              <span className="material-symbols-outlined text-[18px]">mail</span>
              Contact support
            </a>
          </div>
        </div>
      )}
    </div>
  );
}
