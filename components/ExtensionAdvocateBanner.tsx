'use client';

import React, { useEffect, useState } from 'react';
import { Sparkles, ArrowRight, X } from 'lucide-react';
import { EXTENSION_URL } from '@/lib/links';

export const ExtensionAdvocateBanner: React.FC = () => {
  const [isVisible, setIsVisible] = useState(false);
  const [isDismissed, setIsDismissed] = useState(false);

  useEffect(() => {
    const handleScroll = () => {
      if (window.scrollY > 300 && !isDismissed) {
        setIsVisible(true);
      }
    };
    window.addEventListener('scroll', handleScroll);
    return () => window.removeEventListener('scroll', handleScroll);
  }, [isDismissed]);

  if (!isVisible || isDismissed) return null;

  return (
    <div className="fixed bottom-5 left-1/2 -translate-x-1/2 z-40 w-11/12 max-w-xl animate-bounce-in">
      <div className="bg-slate-900 text-white p-4 rounded-2xl shadow-2xl border border-slate-700 flex items-center justify-between gap-4">
        <div className="flex items-center space-x-3">
          <div className="w-10 h-10 rounded-xl bg-emerald-500/20 text-emerald-400 flex items-center justify-center shrink-0">
            <Sparkles className="w-5 h-5" />
          </div>
          <div>
            <h4 className="font-bold text-sm">Want this on Amazon directly?</h4>
            <p className="text-xs text-slate-300">Install the free Chrome Extension to see price timings on every product.</p>
          </div>
        </div>

        <div className="flex items-center space-x-2 shrink-0">
          <a
            href={EXTENSION_URL}
            target="_blank"
            rel="noreferrer"
            className="flex items-center space-x-1.5 px-3.5 py-2 bg-emerald-500 hover:bg-emerald-600 text-slate-950 font-extrabold text-xs rounded-xl transition"
          >
            <span>Add to Chrome</span>
            <ArrowRight className="w-3.5 h-3.5" />
          </a>
          <button
            onClick={() => setIsDismissed(true)}
            className="p-1.5 text-slate-400 hover:text-white transition"
          >
            <X className="w-4 h-4" />
          </button>
        </div>
      </div>
    </div>
  );
};
