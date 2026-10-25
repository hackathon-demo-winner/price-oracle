'use client';

import React, { useEffect, useState } from 'react';
import { fetchMyAlerts, changeMyAlert } from '@/lib/alerts-api';
import { alertProductLink } from '@/lib/retailers';
import { Navbar } from '@/components/Navbar';
import { Footer } from '@/components/Footer';
import { QuickAlertBar } from '@/components/QuickAlertBar';
import { useAuth } from '@/lib/auth-context';
import { Bell, Trash2, Edit2, Check, ExternalLink, TrendingDown, Sparkles, AlertCircle, RefreshCw } from 'lucide-react';

interface AlertItem {
  id: string;
  targetType: string;
  targetValue: string;
  retailer?: string | null;
  productUrl?: string | null;
  productTitle: string;
  currentPriceAtCreation?: number;
  targetPrice?: number;
  triggerOnPredictedDrop: boolean;
  status: 'active' | 'triggered' | 'paused';
  createdAt: string;
}

const INITIAL_DEMO_ALERTS: AlertItem[] = [
  {
    id: 'demo-1',
    targetType: 'keyword',
    targetValue: 'hair pin',
    productTitle: 'Hair Pin Gold Minimalist Set (12-pack)',
    currentPriceAtCreation: 14.99,
    targetPrice: 11.50,
    triggerOnPredictedDrop: true,
    status: 'active',
    createdAt: new Date().toISOString()
  },
  {
    id: 'demo-2',
    targetType: 'asin',
    targetValue: 'B0CX22ZW1T',
    productTitle: 'Apple MacBook Air 13-inch (M3 Chip)',
    currentPriceAtCreation: 899.00,
    targetPrice: 849.00,
    triggerOnPredictedDrop: true,
    status: 'active',
    createdAt: new Date().toISOString()
  },
  {
    id: 'demo-3',
    targetType: 'keyword',
    targetValue: 'wireless mechanical keyboard',
    productTitle: 'Wireless Mechanical Keyboard RGB',
    currentPriceAtCreation: 79.99,
    targetPrice: 65.00,
    triggerOnPredictedDrop: false,
    status: 'active',
    createdAt: new Date().toISOString()
  }
];

// Mock-up of the AI market check for an alert. The wording is built from the alert's own
// numbers; it does not call the research agent (that runs from the Search page).
function mockMarketCheck(alert: AlertItem): { outlook: string; summary: string } | null {
  if (alert.targetType === 'category' || alert.targetType === 'keyword') return null;

  const price = alert.currentPriceAtCreation;
  const target = alert.targetPrice;
  if (!price) return null;

  if (!target) {
    return {
      outlook: 'Watching for any dip',
      summary: `No target set, so this alert fires on a 5% dip to about $${(price * 0.95).toFixed(2)}. Dips of that size usually arrive with a sale event rather than on an ordinary day.`
    };
  }

  const gap = ((price - target) / price) * 100;
  if (gap <= 0) {
    return { outlook: 'Already at your target', summary: `The price when you set this alert ($${price.toFixed(2)}) was at or below your target of $${target.toFixed(2)}.` };
  }
  if (gap <= 10) {
    return {
      outlook: 'Target within reach',
      summary: `Your target is ${gap.toFixed(0)}% below the $${price.toFixed(2)} starting price. Drops of this size are common during routine promotions, so this one has a fair chance of triggering in the coming weeks.`
    };
  }
  if (gap <= 25) {
    return {
      outlook: 'Likely needs a sale event',
      summary: `Your target is ${gap.toFixed(0)}% below the $${price.toFixed(2)} starting price. That usually takes a major sale event or a newer model arriving, so expect to wait for one.`
    };
  }
  return {
    outlook: 'Ambitious target',
    summary: `Your target is ${gap.toFixed(0)}% below the $${price.toFixed(2)} starting price. A drop that deep is rare outside clearance pricing; consider a target closer to $${(price * 0.85).toFixed(2)}.`
  };
}

export default function AlertsPage() {
  const { user, signInWithGoogle, loading: authLoading } = useAuth();
  const [alerts, setAlerts] = useState<AlertItem[]>([]);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editPrice, setEditPrice] = useState<string>('');
  const [editTitle, setEditTitle] = useState<string>('');

  useEffect(() => {
    // Load from local storage or fallback demo
    const saved = localStorage.getItem('price_oracle_alerts');
    if (saved) {
      try {
        setAlerts(JSON.parse(saved));
      } catch {
        setAlerts(INITIAL_DEMO_ALERTS);
      }
    } else {
      setAlerts(INITIAL_DEMO_ALERTS);
    }
  }, []);

  // Signed-in users see the alerts stored in the database, on any device
  useEffect(() => {
    fetchMyAlerts(user).then(serverAlerts => {
      if (serverAlerts) saveAlerts(serverAlerts as AlertItem[]);
    });
  }, [user]);

  const saveAlerts = (newAlerts: AlertItem[]) => {
    setAlerts(newAlerts);
    localStorage.setItem('price_oracle_alerts', JSON.stringify(newAlerts));
  };

  const handleDelete = (id: string) => {
    const updated = alerts.filter(a => a.id !== id);
    saveAlerts(updated);
    changeMyAlert(user, { action: 'delete', alertId: id });
  };

  const handleToggleStatus = (id: string) => {
    const updated = alerts.map(a => {
      if (a.id === id) {
        return {
          ...a,
          status: (a.status === 'active' ? 'paused' : 'active') as 'active' | 'paused'
        };
      }
      return a;
    });
    saveAlerts(updated);
    const toggled = updated.find(a => a.id === id);
    if (toggled) changeMyAlert(user, { action: 'update', alertId: id, status: toggled.status as 'active' | 'paused' });
  };

  const handleStartEdit = (alert: AlertItem) => {
    setEditingId(alert.id);
    setEditPrice(alert.targetPrice ? alert.targetPrice.toString() : '');
    setEditTitle(alert.productTitle);
  };

  const handleSaveEdit = (id: string) => {
    const updated = alerts.map(a => {
      if (a.id === id) {
        return {
          ...a,
          productTitle: editTitle || a.productTitle,
          targetPrice: editPrice ? parseFloat(editPrice) : undefined
        };
      }
      return a;
    });
    saveAlerts(updated);
    changeMyAlert(user, { action: 'update', alertId: id, targetPrice: editPrice ? parseFloat(editPrice) : null, productTitle: editTitle });
    setEditingId(null);
  };

  const handleReload = async () => {
    const serverAlerts = await fetchMyAlerts(user);
    if (serverAlerts) {
      saveAlerts(serverAlerts as AlertItem[]);
      return;
    }
    const saved = localStorage.getItem('price_oracle_alerts');
    if (saved) {
      setAlerts(JSON.parse(saved));
    }
  };

  return (
    <div className="min-h-screen flex flex-col justify-between bg-slate-50">
      <Navbar />

      <main className="flex-1 max-w-6xl w-full mx-auto px-4 py-8 sm:py-12">
        {/* Quick Alert Notifier Bar */}
        <div className="mb-10">
          <QuickAlertBar onAlertCreated={handleReload} />
        </div>

        {/* Header & User Status */}
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 mb-8 bg-white p-6 rounded-3xl border border-slate-200/80 shadow-sm">
          <div>
            <div className="flex items-center space-x-2 text-emerald-600 font-bold text-xs uppercase tracking-wider">
              <Bell className="w-4 h-4" />
              <span>Sentinel Surveillance Feed</span>
            </div>
            <h1 className="text-2xl sm:text-3xl font-extrabold text-slate-900 mt-1">
              My Price Alerts & Watchlist
            </h1>
            <p className="text-slate-500 text-sm mt-1">
              Manage your active Amazon item and keyword monitors. Change thresholds or pause monitoring at any time.
            </p>
          </div>

          {!user && (
            <div className="flex items-center space-x-3 bg-amber-50 border border-amber-200 p-3 rounded-2xl">
              <AlertCircle className="w-5 h-5 text-amber-600 shrink-0" />
              <div className="text-xs">
                <p className="font-bold text-amber-900">Signed in as Guest</p>
                <p className="text-amber-700">Connect Google to sync alerts across devices.</p>
              </div>
              <button
                onClick={signInWithGoogle}
                className="px-3 py-1.5 bg-amber-600 hover:bg-amber-700 text-white font-bold text-xs rounded-xl transition"
              >
                Sign In
              </button>
            </div>
          )}
        </div>

        {/* Alerts List */}
        {alerts.length === 0 ? (
          <div className="bg-white rounded-3xl border border-slate-200 p-12 text-center">
            <Bell className="w-12 h-12 text-slate-300 mx-auto mb-3" />
            <h3 className="text-lg font-bold text-slate-800">No Alerts Configured Yet</h3>
            <p className="text-slate-500 text-sm mt-1 max-w-sm mx-auto">
              Use the alert bar above to track any product (e.g. &ldquo;hair pin&rdquo;, an Amazon URL, or keyword).
            </p>
          </div>
        ) : (
          <div className="grid grid-cols-1 gap-4">
            {alerts.map((alert) => (
              <div
                key={alert.id}
                className="bg-white rounded-2xl border border-slate-200 p-5 shadow-sm hover:shadow-md transition flex flex-col md:flex-row md:items-center justify-between gap-4"
              >
                {/* Alert Info */}
                <div className="flex-1">
                  <div className="flex items-center space-x-2 mb-2">
                    <span className="text-[10px] font-bold px-2 py-0.5 rounded-full bg-slate-100 text-slate-700 uppercase">
                      {alert.targetType}: {alert.targetValue}
                      {alertProductLink(alert) && (
                        <a href={alertProductLink(alert)!.url} target="_blank" rel="noreferrer" className="ml-2 normal-case underline hover:text-slate-700">
                          {alertProductLink(alert)!.url.replace('https://www.', '')}
                        </a>
                      )}
                    </span>
                    <span
                      className={`text-[10px] font-bold px-2 py-0.5 rounded-full uppercase border ${
                        alert.status === 'active'
                          ? 'bg-emerald-50 text-emerald-700 border-emerald-200'
                          : 'bg-slate-100 text-slate-500 border-slate-200'
                      }`}
                    >
                      {alert.status}
                    </span>
                    {alert.triggerOnPredictedDrop && (
                      <span className="text-[10px] font-bold px-2 py-0.5 rounded-full bg-teal-50 text-teal-700 border border-teal-200">
                        ⚡ Predictive Early Alert
                      </span>
                    )}
                  </div>

                  {editingId === alert.id ? (
                    <div className="flex flex-col sm:flex-row gap-2 my-2">
                      <input
                        type="text"
                        value={editTitle}
                        onChange={(e) => setEditTitle(e.target.value)}
                        className="px-3 py-1.5 border border-slate-300 rounded-xl text-sm font-semibold flex-1"
                        placeholder="Item Title"
                      />
                      <input
                        type="number"
                        step="0.01"
                        value={editPrice}
                        onChange={(e) => setEditPrice(e.target.value)}
                        className="px-3 py-1.5 border border-slate-300 rounded-xl text-sm font-semibold w-32"
                        placeholder="Target $"
                      />
                      <button
                        onClick={() => handleSaveEdit(alert.id)}
                        className="px-3 py-1.5 bg-emerald-600 text-white rounded-xl font-bold text-xs flex items-center space-x-1"
                      >
                        <Check className="w-3.5 h-3.5" />
                        <span>Save</span>
                      </button>
                    </div>
                  ) : (
                    <div>
                      <h3 className="font-extrabold text-base text-slate-900">{alert.productTitle}</h3>
                      <div className="mt-1 flex flex-wrap items-center gap-4 text-xs text-slate-500 font-medium">
                        {alert.currentPriceAtCreation && (
                          <span>Initial: <strong>${alert.currentPriceAtCreation.toFixed(2)}</strong></span>
                        )}
                        <span>
                          Target: <strong className="text-emerald-700 font-bold">{alert.targetPrice ? `$${alert.targetPrice.toFixed(2)}` : 'Any Price Drop'}</strong>
                        </span>
                        <span>Frequency: <strong>Every 4 Hours</strong></span>
                      </div>
                      {mockMarketCheck(alert) && (
                        <div className="mt-3 rounded-xl bg-slate-50 border border-slate-200 p-3 text-xs text-slate-600">
                          <p className="flex items-center space-x-1.5 font-bold text-slate-800">
                            <Sparkles className="w-3.5 h-3.5 text-emerald-600" />
                            <span>AI market check: {mockMarketCheck(alert)!.outlook}</span>
                            <span className="font-semibold text-slate-400">· preview</span>
                          </p>
                          <p className="mt-1 leading-relaxed">{mockMarketCheck(alert)!.summary}</p>
                        </div>
                      )}
                    </div>
                  )}
                </div>

                {/* Actions */}
                <div className="flex items-center space-x-2 shrink-0 pt-3 md:pt-0 border-t md:border-t-0 border-slate-100">
                  <button
                    onClick={() => handleToggleStatus(alert.id)}
                    className={`px-3 py-1.5 rounded-xl text-xs font-bold transition border ${
                      alert.status === 'active'
                        ? 'border-slate-200 text-slate-600 hover:bg-slate-50'
                        : 'border-emerald-200 bg-emerald-50 text-emerald-700 hover:bg-emerald-100'
                    }`}
                  >
                    {alert.status === 'active' ? 'Pause' : 'Resume'}
                  </button>

                  <button
                    onClick={() => handleStartEdit(alert)}
                    className="p-2 text-slate-500 hover:text-slate-800 border border-slate-200 hover:border-slate-300 rounded-xl transition"
                    title="Edit Threshold"
                  >
                    <Edit2 className="w-4 h-4" />
                  </button>

                  <button
                    onClick={() => handleDelete(alert.id)}
                    className="p-2 text-slate-400 hover:text-rose-600 border border-slate-200 hover:border-rose-200 hover:bg-rose-50 rounded-xl transition"
                    title="Delete Alert"
                  >
                    <Trash2 className="w-4 h-4" />
                  </button>

                  {alertProductLink(alert) && (
                    <a
                      href={alertProductLink(alert)!.url}
                      target="_blank"
                      rel="noreferrer"
                      className="p-2 text-slate-500 hover:text-slate-800 border border-slate-200 rounded-xl transition"
                      title={`View on ${alertProductLink(alert)!.retailerName}`}
                    >
                      <ExternalLink className="w-4 h-4" />
                    </a>
                  )}
                </div>
              </div>
            ))}
          </div>
        )}
      </main>

      <Footer />
    </div>
  );
}
