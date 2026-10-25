'use client';

import React, { useState } from 'react';
import { X, Bell, CheckCircle2, AlertCircle } from 'lucide-react';
import { useAuth } from '@/lib/auth-context';
import { authHeaders } from '@/lib/alerts-api';

interface AlertModalProps {
  isOpen: boolean;
  onClose: () => void;
  initialValue?: string;
}

export const AlertModal: React.FC<AlertModalProps> = ({ isOpen, onClose, initialValue = '' }) => {
  const [targetType, setTargetType] = useState<'asin' | 'category'>('asin');
  const [targetValue, setTargetValue] = useState(initialValue);
  const [email, setEmail] = useState('');
  const [targetPrice, setTargetPrice] = useState('');
  const [triggerOnPredictedDrop, setTriggerOnPredictedDrop] = useState(true);
  const [loading, setLoading] = useState(false);
  const [success, setSuccess] = useState(false);
  const [errorMsg, setErrorMsg] = useState('');
  const { user } = useAuth();

  if (!isOpen) return null;

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!email || !targetValue) return;

    setLoading(true);
    setErrorMsg('');

    try {
      const res = await fetch('/api/create-alert', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...(await authHeaders(user)) },
        body: JSON.stringify({
          email,
          targetType,
          targetValue,
          targetPrice: targetPrice ? parseFloat(targetPrice) : null,
          triggerOnPredictedDrop
        })
      });

      const data = await res.json();
      if (data.success) {
        // Keep a copy for the My Alerts page, which reads this browser's list when not signed in
        try {
          const saved = localStorage.getItem('price_oracle_alerts');
          const existing = saved ? JSON.parse(saved) : [];
          localStorage.setItem('price_oracle_alerts', JSON.stringify([{ id: data.alertId, ...data.alert }, ...existing]));
        } catch {}
        setSuccess(true);
      } else {
        setErrorMsg(data.error || 'Failed to create alert');
      }
    } catch {
      setErrorMsg('Could not reach the alert service. Please try again.');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-slate-900/60 backdrop-blur-sm">
      <div className="bg-white rounded-3xl max-w-lg w-full p-6 sm:p-8 shadow-2xl border border-slate-100 relative">
        <button
          onClick={onClose}
          className="absolute top-5 right-5 text-slate-400 hover:text-slate-600 p-1 rounded-full transition"
        >
          <X className="w-5 h-5" />
        </button>

        {success ? (
          <div className="text-center py-6">
            <CheckCircle2 className="w-16 h-16 text-emerald-500 mx-auto mb-4" />
            <h3 className="text-2xl font-black text-slate-900">Price Alert Configured!</h3>
            <p className="mt-2 text-sm text-slate-600">
              Our background worker is monitoring Amazon and Best Buy every few hours. We will email <strong>{email}</strong> the second the item hits your target or an imminent drop is forecast.
            </p>
            <button
              onClick={() => {
                setSuccess(false);
                onClose();
              }}
              className="mt-6 w-full py-3 bg-slate-900 hover:bg-slate-800 text-white font-semibold rounded-xl transition"
            >
              Done
            </button>
          </div>
        ) : (
          <div>
            <div className="flex items-center space-x-2 text-emerald-600 mb-2">
              <Bell className="w-5 h-5" />
              <span className="text-xs font-bold uppercase tracking-wider">Automated Price Sentinel</span>
            </div>
            <h3 className="text-2xl font-extrabold text-slate-900">Set Custom Price Alert</h3>
            <p className="text-sm text-slate-500 mt-1">
              Track a specific Amazon or Best Buy product, or an entire category.
            </p>

            <form onSubmit={handleSubmit} className="mt-6 space-y-4">
              {/* Type Switcher */}
              <div className="grid grid-cols-2 gap-2 p-1 bg-slate-100 rounded-xl text-xs font-bold">
                <button
                  type="button"
                  onClick={() => setTargetType('asin')}
                  className={`py-2 rounded-lg transition ${
                    targetType === 'asin' ? 'bg-white text-slate-900 shadow-sm' : 'text-slate-500 hover:text-slate-800'
                  }`}
                >
                  Specific Product / URL
                </button>
                <button
                  type="button"
                  onClick={() => setTargetType('category')}
                  className={`py-2 rounded-lg transition ${
                    targetType === 'category' ? 'bg-white text-slate-900 shadow-sm' : 'text-slate-500 hover:text-slate-800'
                  }`}
                >
                  Product Category
                </button>
              </div>

              {/* Target Input */}
              <div>
                <label className="block text-xs font-bold text-slate-700 uppercase mb-1">
                  {targetType === 'asin' ? 'Amazon or Best Buy URL, ASIN, or SKU' : 'Select Category'}
                </label>
                {targetType === 'asin' ? (
                  <input
                    type="text"
                    value={targetValue}
                    onChange={(e) => setTargetValue(e.target.value)}
                    placeholder="Amazon or Best Buy product URL, ASIN, or SKU..."
                    className="w-full px-4 py-2.5 rounded-xl border border-slate-200 text-sm focus:outline-none focus:ring-2 focus:ring-emerald-500/50"
                    required
                  />
                ) : (
                  <select
                    value={targetValue}
                    onChange={(e) => setTargetValue(e.target.value)}
                    className="w-full px-4 py-2.5 rounded-xl border border-slate-200 text-sm focus:outline-none focus:ring-2 focus:ring-emerald-500/50 bg-white"
                    required
                  >
                    <option value="">-- Choose Category --</option>
                    <option value="Electronics">Electronics (Laptops, Audio, Cameras)</option>
                    <option value="Gaming">Gaming (Consoles, Peripherals)</option>
                    <option value="Home & Kitchen">Home & Kitchen</option>
                    <option value="Apparel">Apparel & Footwear</option>
                  </select>
                )}
              </div>

              {/* Target Price Threshold */}
              <div>
                <label className="block text-xs font-bold text-slate-700 uppercase mb-1">
                  Target Price ($) (Optional)
                </label>
                <input
                  type="number"
                  step="0.01"
                  value={targetPrice}
                  onChange={(e) => setTargetPrice(e.target.value)}
                  placeholder="e.g. 175.00 (Leave blank to notify on any dip)"
                  className="w-full px-4 py-2.5 rounded-xl border border-slate-200 text-sm focus:outline-none focus:ring-2 focus:ring-emerald-500/50"
                />
              </div>

              {/* Checkbox for predictive early alert */}
              <div className="flex items-start space-x-3 bg-emerald-50/60 p-3 rounded-xl border border-emerald-100">
                <input
                  type="checkbox"
                  id="predictiveDrop"
                  checked={triggerOnPredictedDrop}
                  onChange={(e) => setTriggerOnPredictedDrop(e.target.checked)}
                  className="mt-1 h-4 w-4 rounded border-slate-300 text-emerald-600 focus:ring-emerald-500"
                />
                <label htmlFor="predictiveDrop" className="text-xs text-slate-700 leading-snug">
                  <strong>Enable Predictive Drop Warning:</strong> Alert me if our prediction market engine detects high odds (&gt;75%) of a price drop in the next 14–30 days.
                </label>
              </div>

              {/* Email Input */}
              <div>
                <label className="block text-xs font-bold text-slate-700 uppercase mb-1">
                  Your Notification Email
                </label>
                <input
                  type="email"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  placeholder="your.email@example.com"
                  className="w-full px-4 py-2.5 rounded-xl border border-slate-200 text-sm focus:outline-none focus:ring-2 focus:ring-emerald-500/50"
                  required
                />
              </div>

              {errorMsg && (
                <div className="text-xs text-rose-600 flex items-center space-x-1">
                  <AlertCircle className="w-4 h-4" />
                  <span>{errorMsg}</span>
                </div>
              )}

              <button
                type="submit"
                disabled={loading}
                className="w-full py-3.5 bg-emerald-600 hover:bg-emerald-700 text-white font-bold rounded-xl shadow-md transition"
              >
                {loading ? 'Activating Sentinel...' : 'Activate Deal Alert'}
              </button>
            </form>
          </div>
        )}
      </div>
    </div>
  );
};
