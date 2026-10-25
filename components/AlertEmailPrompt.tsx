'use client';

import React, { useState } from 'react';
import type { User } from 'firebase/auth';

const STORAGE_KEY = 'price_oracle_email';

// The address to send an alert to without asking: a really signed-in user's email,
// or the one this browser used last time. The demo fallback user has no real address.
export function knownAlertEmail(user: User | null): string | null {
  if (user?.email && typeof user.getIdToken === 'function') return user.email;
  try {
    return localStorage.getItem(STORAGE_KEY);
  } catch {
    return null;
  }
}

interface AlertEmailPromptProps {
  productTitle: string;
  onSubmit: (email: string) => void;
  onCancel: () => void;
}

export const AlertEmailPrompt: React.FC<AlertEmailPromptProps> = ({ productTitle, onSubmit, onCancel }) => {
  const [email, setEmail] = useState('');

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    const address = email.trim();
    if (!address) return;
    try {
      localStorage.setItem(STORAGE_KEY, address);
    } catch {}
    onSubmit(address);
  };

  return (
    <form onSubmit={handleSubmit} className="rounded-2xl bg-emerald-50/60 border border-emerald-100 p-4 text-left">
      <label className="block text-xs font-bold text-slate-700 mb-2">
        Where should we email you about <span className="font-extrabold">{productTitle}</span>?
      </label>
      <div className="flex flex-col sm:flex-row gap-2">
        <input
          type="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          placeholder="your.email@example.com"
          className="flex-1 px-4 py-2.5 rounded-xl border border-slate-200 text-sm focus:outline-none focus:ring-2 focus:ring-emerald-500/50"
          autoFocus
          required
        />
        <button type="submit" className="px-4 py-2.5 bg-emerald-600 hover:bg-emerald-700 text-white font-bold text-xs rounded-xl transition">
          Save alert
        </button>
        <button type="button" onClick={onCancel} className="px-4 py-2.5 border border-slate-200 hover:border-slate-300 text-slate-600 font-semibold text-xs rounded-xl transition">
          Cancel
        </button>
      </div>
    </form>
  );
};
