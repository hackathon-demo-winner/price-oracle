'use client';

import React, { useState } from 'react';
import { ArrowRight, Bell, ShieldCheck, TrendingDown, Sparkles } from 'lucide-react';
import { EXTENSION_URL } from '@/lib/links';

interface HeroProps {
  onOpenAlertModal: () => void;
}

export const Hero: React.FC<HeroProps> = ({ onOpenAlertModal }) => {
  const [email, setEmail] = useState('');
  const [status, setStatus] = useState<'idle' | 'loading' | 'success' | 'error'>('idle');

  const handleSubscribe = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!email) return;
    setStatus('loading');
    try {
      const res = await fetch('/api/subscribe', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email, source: 'landing_hero' })
      });
      if (!res.ok) throw new Error('Subscribe failed');
      setStatus('success');
      setEmail('');
    } catch {
      setStatus('error');
    }
  };

  return (
    <div className="relative pt-12 pb-16 lg:pt-20 lg:pb-24 overflow-hidden">
      {/* Background glow effects */}
      <div className="absolute top-0 left-1/2 -translate-x-1/2 w-full max-w-7xl h-96 bg-gradient-to-b from-emerald-100/60 via-teal-50/30 to-transparent blur-3xl pointer-events-none -z-10" />

      <div className="max-w-4xl mx-auto px-4 text-center">
        {/* Top Announcement Pill */}
        <div className="inline-flex items-center space-x-2 px-3.5 py-1.5 rounded-full bg-emerald-50 border border-emerald-200 text-emerald-800 text-xs sm:text-sm font-semibold mb-6 shadow-sm">
          <Sparkles className="w-4 h-4 text-emerald-600 animate-pulse" />
          <span>AI Price Forecasting powered by context.dev & prediction markets</span>
        </div>

        {/* Main Pitch */}
        <h1 className="text-4xl sm:text-6xl font-black text-slate-900 tracking-tight leading-[1.15]">
          Stop Overpaying on Amazon. <br />
          <span className="bg-gradient-to-r from-emerald-600 to-teal-500 bg-clip-text text-transparent">
            Know Exactly When to Buy.
          </span>
        </h1>

        <p className="mt-6 text-lg sm:text-xl text-slate-600 max-w-2xl mx-auto leading-relaxed">
          Get real-time 30-day price projections directly on Amazon with our free Chrome Extension, or receive automated email alerts the second prices hit historical lows.
        </p>

        {/* Action Buttons */}
        <div className="mt-8 flex flex-col sm:flex-row items-center justify-center gap-4">
          <a
            id="install-extension"
            href={EXTENSION_URL}
            target="_blank"
            rel="noreferrer"
            className="w-full sm:w-auto flex items-center justify-center space-x-3 bg-emerald-600 hover:bg-emerald-700 text-white font-bold text-base px-7 py-3.5 rounded-2xl shadow-lg shadow-emerald-600/25 transition duration-200 hover:-translate-y-0.5"
          >
            <svg className="w-5 h-5 fill-current" viewBox="0 0 24 24">
              <path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm-1 17.93c-3.95-.49-7-3.85-7-7.93 0-.62.08-1.21.21-1.79L9 15v1c0 1.1.9 2 2 2v1.93zm6.9-2.54c-.26-.81-1-1.39-1.9-1.39h-1v-3c0-.55-.45-1-1-1H8v-2h2c.55 0 1-.45 1-1V7h2c1.1 0 2-.9 2-2v-.41c2.93 1.19 5 4.06 5 7.41 0 2.08-.8 3.97-2.1 5.39z"/>
            </svg>
            <span>Add to Chrome — Free Extension</span>
          </a>

          <button
            onClick={onOpenAlertModal}
            className="w-full sm:w-auto flex items-center justify-center space-x-2.5 bg-white hover:bg-slate-50 text-slate-800 font-bold text-base px-6 py-3.5 rounded-2xl border border-slate-300 shadow-sm transition hover:border-slate-400"
          >
            <Bell className="w-5 h-5 text-emerald-600" />
            <span>Set Deal Alert</span>
          </button>
        </div>

        {/* Quick Email Signup for Hot Drops */}
        <div className="mt-12 max-w-md mx-auto bg-white/90 backdrop-blur-md p-2 rounded-2xl border border-slate-200 shadow-md">
          <form onSubmit={handleSubscribe} className="flex flex-col sm:flex-row gap-2">
            <input
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="Enter email for daily predicted drops..."
              className="flex-1 px-4 py-2.5 text-sm rounded-xl border border-slate-200 focus:outline-none focus:ring-2 focus:ring-emerald-500/50"
              required
            />
            <button
              type="submit"
              disabled={status === 'loading'}
              className="px-5 py-2.5 rounded-xl bg-slate-900 hover:bg-slate-800 text-white font-semibold text-sm transition flex items-center justify-center space-x-1.5"
            >
              <span>{status === 'loading' ? 'Subscribing...' : 'Get Hot Deals'}</span>
              <ArrowRight className="w-4 h-4" />
            </button>
          </form>
          {status === 'error' && (
            <p className="text-xs text-rose-600 font-medium mt-2">
              Could not subscribe. Please try again.
            </p>
          )}
          {status === 'success' && (
            <p className="text-xs text-emerald-700 font-medium mt-2">
              ✓ Subscribed! You will receive verified Amazon price drops.
            </p>
          )}
        </div>

        {/* Trust Badges */}
        <div className="mt-8 flex flex-wrap items-center justify-center gap-6 text-xs text-slate-500 font-medium">
          <span className="flex items-center space-x-1.5">
            <ShieldCheck className="w-4 h-4 text-emerald-600" />
            <span>Zero Spam Guarantee</span>
          </span>
          <span className="flex items-center space-x-1.5">
            <TrendingDown className="w-4 h-4 text-teal-600" />
            <span>5-Tier Quantile Math (P5-P95)</span>
          </span>
          <span className="flex items-center space-x-1.5">
            <Sparkles className="w-4 h-4 text-amber-500" />
            <span>Verified by context.dev</span>
          </span>
        </div>
      </div>
    </div>
  );
};
