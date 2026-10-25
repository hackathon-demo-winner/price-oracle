'use client';

import React, { useState, useEffect, useRef } from 'react';
import { Search, Bell, Sparkles, Check, Star, ExternalLink, Loader2 } from 'lucide-react';
import { useAuth } from '@/lib/auth-context';
import { authHeaders } from '@/lib/alerts-api';
import { AlertEmailPrompt, knownAlertEmail } from '@/components/AlertEmailPrompt';
import { useForecasts, describeForecast } from '@/lib/forecast-api';
import { searchProducts, SearchProductResult } from '@/lib/search-service';

export type ProductResult = SearchProductResult;

interface QuickAlertBarProps {
  onAlertCreated?: () => void;
}

export const QuickAlertBar: React.FC<QuickAlertBarProps> = ({ onAlertCreated }) => {
  const { user, signInWithGoogle } = useAuth();
  const [query, setQuery] = useState('');
  const [results, setResults] = useState<ProductResult[]>([]);
  const forecasts = useForecasts(results);
  const [loadingSearch, setLoadingSearch] = useState(false);
  const [settingAlertId, setSettingAlertId] = useState<string | null>(null);
  const [successAlertId, setSuccessAlertId] = useState<string | null>(null);
  const [failedAlertId, setFailedAlertId] = useState<string | null>(null);
  // The product waiting for an email address before its alert can be saved
  const [emailPromptFor, setEmailPromptFor] = useState<ProductResult | null>(null);
  const [showDropdown, setShowDropdown] = useState(false);
  const dropdownRef = useRef<HTMLDivElement>(null);

  // Close dropdown on outside click
  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (dropdownRef.current && !dropdownRef.current.contains(e.target as Node)) {
        setShowDropdown(false);
      }
    };
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  // Instant responsive search when user types
  useEffect(() => {
    if (!query.trim() || query.trim().length < 2) {
      setResults([]);
      setShowDropdown(false);
      return;
    }

    setLoadingSearch(true);
    const timer = setTimeout(() => {
      const items = searchProducts(query);
      setResults(items);
      setShowDropdown(true);
      setLoadingSearch(false);
    }, 150);

    return () => clearTimeout(timer);
  }, [query]);

  // Handler to set alert on a specific result
  const handleSetAlert = async (product: ProductResult, emailOverride?: string) => {
    // No sign-in needed: use the known address, or ask for one
    const email = emailOverride || knownAlertEmail(user);
    if (!email) {
      setEmailPromptFor(product);
      return;
    }
    setEmailPromptFor(null);

    setSettingAlertId(product.id);

    try {
      // 1. Post to backend
      const res = await fetch('/api/create-alert', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...(await authHeaders(user)) },
        body: JSON.stringify({
          email,
          targetType: 'asin',
          targetValue: product.asin,
          productTitle: product.title,
          targetPrice: Number((product.price * 0.9).toFixed(2)), // auto-set to 10% below
          triggerOnPredictedDrop: true
        })
      });
      if (!res.ok) throw new Error('Alert was not saved');

      // 2. Save into local storage so My Alerts page shows it immediately
      const saved = localStorage.getItem('price_oracle_alerts');
      const existing = saved ? JSON.parse(saved) : [];
      const newAlert = {
        id: `alert-${Date.now()}`,
        targetType: 'asin',
        targetValue: product.asin,
        retailer: product.retailer === 'Best Buy' ? 'bestbuy' : 'amazon',
        productUrl: product.amazonUrl,
        productTitle: product.title,
        currentPriceAtCreation: product.price,
        targetPrice: Number((product.price * 0.9).toFixed(2)),
        triggerOnPredictedDrop: true,
        status: 'active',
        createdAt: new Date().toISOString()
      };
      localStorage.setItem('price_oracle_alerts', JSON.stringify([newAlert, ...existing]));

      setSuccessAlertId(product.id);
      if (onAlertCreated) onAlertCreated();
      setTimeout(() => setSuccessAlertId(null), 4000);
    } catch (err) {
      setFailedAlertId(product.id);
      setTimeout(() => setFailedAlertId(null), 4000);
      console.error('Failed to set alert:', err);
    } finally {
      setSettingAlertId(null);
    }
  };

  return (
    <div className="w-full max-w-4xl mx-auto px-4 mt-6 relative" ref={dropdownRef}>
      <div className="bg-gradient-to-r from-emerald-600 via-teal-600 to-slate-900 p-1 rounded-3xl shadow-xl shadow-emerald-950/15">
        <div className="bg-white/95 backdrop-blur-md rounded-2xl p-4 sm:p-5">
          
          <div className="flex items-center justify-between mb-2">
            <div className="flex items-center space-x-2 text-xs font-bold text-emerald-800">
              <Sparkles className="w-4 h-4 text-emerald-600" />
              <span>SEARCH &amp; TRACK: Instant Amazon options with one-click price alert buttons</span>
            </div>
            {loadingSearch && (
              <span className="flex items-center space-x-1 text-xs text-slate-400">
                <Loader2 className="w-3.5 h-3.5 animate-spin" />
                <span>Searching Amazon...</span>
              </span>
            )}
          </div>

          {/* Search Bar Input */}
          <div className="relative w-full">
            <Search className="w-5 h-5 absolute left-4 top-1/2 -translate-y-1/2 text-slate-400" />
            <input
              type="text"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onFocus={() => { if (results.length > 0) setShowDropdown(true); }}
              placeholder="Search Amazon products to track (e.g. hair pin, wireless earbuds, macbook, ASIN)..."
              className="w-full pl-12 pr-10 py-3.5 bg-slate-50 border border-slate-200 rounded-2xl text-base font-medium text-slate-800 placeholder-slate-400 focus:outline-none focus:ring-2 focus:ring-emerald-500/50 focus:bg-white transition"
            />
            {query && (
              <button
                onClick={() => { setQuery(''); setResults([]); setShowDropdown(false); }}
                className="absolute right-3.5 top-1/2 -translate-y-1/2 text-xs text-slate-400 hover:text-slate-600 bg-slate-200 hover:bg-slate-300 w-5 h-5 rounded-full flex items-center justify-center transition"
              >
                ✕
              </button>
            )}
          </div>

          {/* Quick Suggestions Chips */}
          <div className="mt-3 flex flex-wrap items-center gap-2 text-xs">
            <span className="font-semibold text-slate-400">Try searching:</span>
            {['hair pin', 'airpods pro', 'gaming mouse', 'kindle paperwhite', 'coffee maker'].map((chip) => (
              <button
                key={chip}
                onClick={() => setQuery(chip)}
                className="px-2.5 py-1 rounded-lg bg-slate-100 hover:bg-emerald-50 hover:text-emerald-700 text-slate-600 font-medium transition"
              >
                {chip}
              </button>
            ))}
          </div>

        </div>
      </div>

      {/* SEARCH RESULTS DROPDOWN WITH SET ALERT BUTTON ON THE SIDE */}
      {showDropdown && results.length > 0 && (
        <div className="absolute left-4 right-4 mt-2 bg-white rounded-3xl border border-slate-200/90 shadow-2xl z-50 overflow-hidden divide-y divide-slate-100 max-h-[480px] overflow-y-auto animate-fade-in">
          <div className="bg-slate-50/80 px-5 py-2.5 flex items-center justify-between text-xs font-bold text-slate-500">
            <span>SUGGESTED PRODUCTS FOR &ldquo;{query}&rdquo;</span>
            <span>Click &ldquo;Set Alert&rdquo; on any item</span>
          </div>

          {results.map((product) => {
            const isSetting = settingAlertId === product.id;
            const isSuccess = successAlertId === product.id;
            const isFailed = failedAlertId === product.id;

            return (
              <div
                key={product.id}
                className="p-4 hover:bg-slate-50/90 transition flex flex-col sm:flex-row sm:items-center justify-between gap-4"
              >
                {/* Product Thumbnail & Details */}
                <div className="flex items-center space-x-3.5 flex-1 min-w-0">
                  <img
                    src={product.image}
                    alt={product.title}
                    className="w-14 h-14 object-cover rounded-xl border border-slate-200 shrink-0 shadow-sm"
                  />
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center space-x-2">
                      <span className="text-[10px] font-bold px-1.5 py-0.5 rounded bg-emerald-100 text-emerald-800">
                        {product.asin}
                      </span>
                      <span className="text-[10px] font-semibold text-slate-400">
                        {product.category}
                      </span>
                      <div className="flex items-center text-[11px] text-amber-500 font-bold space-x-0.5">
                        <Star className="w-3 h-3 fill-amber-400 text-amber-400" />
                        <span>{product.rating}</span>
                        <span className="text-slate-400 font-normal">({product.reviewsCount})</span>
                      </div>
                    </div>

                    <h4 className="font-bold text-sm text-slate-900 truncate mt-0.5">
                      {product.title}
                    </h4>

                    <div className="flex items-baseline space-x-2 mt-1">
                      <span className="text-lg font-black text-slate-900">${product.price.toFixed(2)}</span>
                      {product.originalPrice > product.price && (
                        <span className="text-xs text-slate-400 line-through">
                          ${product.originalPrice.toFixed(2)}
                        </span>
                      )}
                      {forecasts[product.id] && (
                        <span className="text-xs font-bold text-slate-600 bg-slate-100 px-1.5 py-0.2 rounded">
                          {describeForecast(forecasts[product.id])}
                        </span>
                      )}
                    </div>
                  </div>
                </div>

                {/* Right Side: Links & Set Alert Action Button */}
                <div className="flex items-center space-x-2 shrink-0">
                  <a
                    href={product.amazonUrl}
                    target="_blank"
                    rel="noreferrer"
                    className="p-2.5 rounded-xl border border-slate-200 hover:border-slate-300 text-slate-400 hover:text-slate-700 transition"
                    title="View on Amazon"
                  >
                    <ExternalLink className="w-4 h-4" />
                  </a>

                  <button
                    onClick={() => handleSetAlert(product)}
                    disabled={isSetting || isSuccess}
                    className={`px-4 py-2.5 rounded-xl font-bold text-xs flex items-center space-x-1.5 transition shadow-sm ${
                      isSuccess
                        ? 'bg-emerald-100 text-emerald-800 border border-emerald-300'
                        : isSetting
                        ? 'bg-slate-100 text-slate-400 cursor-not-allowed'
                        : isFailed
                        ? 'bg-rose-50 text-rose-700 border border-rose-300'
                        : 'bg-emerald-600 hover:bg-emerald-700 text-white shadow-emerald-600/20 hover:shadow'
                    }`}
                  >
                    {isSuccess ? (
                      <>
                        <Check className="w-3.5 h-3.5 text-emerald-700" />
                        <span>Alert Active!</span>
                      </>
                    ) : isSetting ? (
                      <>
                        <Loader2 className="w-3.5 h-3.5 animate-spin" />
                        <span>Tracking...</span>
                      </>
                    ) : (
                      <>
                        <Bell className="w-3.5 h-3.5" />
                        <span>{isFailed ? 'Not saved. Retry' : 'Set Alert'}</span>
                      </>
                    )}
                  </button>
                </div>
              </div>
            );
          })}

          {emailPromptFor && (
            <div className="p-3">
              <AlertEmailPrompt
                productTitle={emailPromptFor.title}
                onSubmit={(email) => handleSetAlert(emailPromptFor, email)}
                onCancel={() => setEmailPromptFor(null)}
              />
            </div>
          )}

          <div className="p-3 bg-slate-50 text-center text-xs text-slate-500 font-medium flex items-center justify-center space-x-4">
            <a href={`/search?q=${encodeURIComponent(query)}`} className="font-bold text-emerald-600 hover:underline">
              View all 15+ detailed results on Search Page &rarr;
            </a>
            <span>•</span>
            <a href="/alerts/" className="text-slate-600 hover:underline">
              My Alerts Watchlist
            </a>
          </div>
        </div>
      )}
    </div>
  );
};
