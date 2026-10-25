'use client';

import React, { useEffect, useState } from 'react';
import { calculatePriceProjection, ProjectionResult } from '@/lib/projection-engine';
import { fetchForecast } from '@/lib/forecast-api';
import { Sparkles, TrendingDown, ArrowDownRight, ArrowUpRight, HelpCircle } from 'lucide-react';

const DEMO_ITEMS = [
  {
    name: 'Apple AirPods Pro 2 (USB-C)',
    category: 'Electronics',
    price: 189.99,
    originalPrice: 249.00,
    discountProb: 0.85,
    sentiment: 0.90
  },
  {
    name: 'Sony WH-1000XM5 Headphones',
    category: 'Electronics',
    price: 398.00,
    originalPrice: 399.99,
    discountProb: 0.92,
    sentiment: 0.84
  },
  {
    name: 'Kindle Paperwhite (16 GB)',
    category: 'Electronics',
    price: 149.99,
    originalPrice: 149.99,
    discountProb: 0.50,
    sentiment: 1.02
  }
];

export const ExtensionDemoWidget: React.FC = () => {
  const [selectedIdx, setSelectedIdx] = useState(0);
  const activeItem = DEMO_ITEMS[selectedIdx];

  // The ML model's forecast for the selected item; the formula fills in until it arrives
  const [modelForecast, setModelForecast] = useState<{ idx: number; forecast: ProjectionResult } | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetchForecast({ currentPrice: activeItem.price, category: activeItem.category, retailer: 'amazon', title: activeItem.name }).then(forecast => {
      if (forecast && !cancelled) setModelForecast({ idx: selectedIdx, forecast });
    });
    return () => {
      cancelled = true;
    };
  }, [selectedIdx]);

  const projection =
    modelForecast?.idx === selectedIdx
      ? modelForecast.forecast
      : calculatePriceProjection(activeItem.price, activeItem.category, activeItem.discountProb, activeItem.sentiment);

  return (
    <div className="max-w-4xl mx-auto px-4 py-12">
      <div className="text-center mb-8">
        <h2 className="text-2xl sm:text-3xl font-extrabold text-slate-900 tracking-tight">
          See What You See on Amazon
        </h2>
        <p className="mt-2 text-slate-600 text-sm sm:text-base">
          Our Chrome Extension injects this live oracle directly beneath the Amazon price box.
        </p>

        {/* Demo Selector Tabs */}
        <div className="mt-6 flex flex-wrap justify-center gap-2">
          {DEMO_ITEMS.map((item, idx) => (
            <button
              key={item.name}
              onClick={() => setSelectedIdx(idx)}
              className={`px-4 py-2 text-xs sm:text-sm font-semibold rounded-xl border transition ${
                selectedIdx === idx
                  ? 'bg-slate-900 text-white border-slate-900 shadow-sm'
                  : 'bg-white text-slate-700 border-slate-200 hover:bg-slate-50'
              }`}
            >
              {item.name}
            </button>
          ))}
        </div>
      </div>

      {/* Mock Amazon Product View Container */}
      <div className="bg-slate-100/80 p-4 sm:p-8 rounded-3xl border border-slate-200 shadow-xl max-w-2xl mx-auto">
        <div className="bg-white rounded-2xl border border-slate-200/90 shadow-md p-6">
          
          {/* Header simulating Amazon */}
          <div className="border-b border-slate-100 pb-4 mb-4">
            <span className="text-xs uppercase tracking-wider text-slate-400 font-bold">Simulated Amazon Page</span>
            <h3 className="text-lg font-bold text-slate-900 mt-1">{activeItem.name}</h3>
            <div className="flex items-baseline space-x-3 mt-2">
              <span className="text-3xl font-black text-slate-900">${activeItem.price.toFixed(2)}</span>
              {activeItem.originalPrice > activeItem.price && (
                <span className="text-sm text-slate-400 line-through">List: ${activeItem.originalPrice.toFixed(2)}</span>
              )}
            </div>
          </div>

          {/* INJECTED PRICEORACLE EXTENSION CARD */}
          <div className="rounded-2xl border-2 border-emerald-500/20 bg-gradient-to-b from-white to-emerald-50/20 p-5 shadow-sm">
            
            {/* Widget Header */}
            <div className="flex items-center justify-between pb-3 border-b border-slate-100">
              <div className="flex items-center space-x-2">
                <span className="w-2.5 h-2.5 rounded-full bg-emerald-500 animate-ping" />
                <span className="font-extrabold text-xs tracking-wider text-slate-800 uppercase">
                  Price Timing Oracle
                </span>
              </div>
              <span className="text-[11px] font-semibold text-emerald-700 bg-emerald-50 px-2.5 py-0.5 rounded-full border border-emerald-200">
                context.dev verified
              </span>
            </div>

            {/* Recommendation Signal */}
            <div className="my-4 flex items-center justify-between">
              <div>
                <span className="text-xs font-medium text-slate-500 block">Timing Signal</span>
                <div className="flex items-center space-x-2 mt-1">
                  <span
                    className={`px-3 py-1 rounded-lg text-xs font-black tracking-wide uppercase border ${
                      projection.recommendation === 'BUY NOW'
                        ? 'bg-emerald-50 text-emerald-700 border-emerald-300'
                        : projection.recommendation === 'WAIT'
                        ? 'bg-rose-50 text-rose-700 border-rose-300'
                        : 'bg-slate-100 text-slate-700 border-slate-300'
                    }`}
                  >
                    {projection.recommendation}
                  </span>
                  <span className="text-xs font-semibold text-slate-700">
                    {projection.subtitle}
                  </span>
                </div>
              </div>
              <div className="text-right">
                <span className="text-xs text-slate-400 block font-medium">Confidence</span>
                <span className="text-sm font-bold text-slate-800">{projection.confidence != null ? `${projection.confidence}%` : 'Not rated'}</span>
              </div>
            </div>

            {/* 5-TIER QUANTILE BAR */}
            <div className="mt-6 pt-2">
              <div className="flex items-center justify-between text-[11px] font-bold text-slate-500 mb-2">
                <span>30-DAY FORECASTED RANGE</span>
                <span className="flex items-center space-x-1">
                  <span>Current: ${activeItem.price}</span>
                  <HelpCircle className="w-3.5 h-3.5 text-slate-400" />
                </span>
              </div>

              {/* Progress Track */}
              <div className="relative w-full h-3 bg-gradient-to-r from-emerald-400 via-amber-400 to-rose-400 rounded-full shadow-inner">
                {/* Marker for Current Price */}
                <div 
                  className="absolute top-1/2 -translate-y-1/2 w-4 h-4 rounded-full bg-slate-900 border-2 border-white shadow-md -ml-2"
                  style={{
                    left: `${Math.min(95, Math.max(5, ((activeItem.price - projection.quantiles.lowest) / (projection.quantiles.highest - projection.quantiles.lowest)) * 100))}%`
                  }}
                />
              </div>

              {/* 5 Labels Grid */}
              <div className="grid grid-cols-5 text-center mt-3 gap-1">
                <div>
                  <span className="block text-[10px] uppercase font-bold text-slate-400">Lowest</span>
                  <span className="text-xs font-bold text-slate-800">${projection.quantiles.lowest}</span>
                </div>
                <div>
                  <span className="block text-[10px] uppercase font-bold text-slate-400">Low</span>
                  <span className="text-xs font-bold text-slate-800">${projection.quantiles.low}</span>
                </div>
                <div>
                  <span className="block text-[10px] uppercase font-bold text-emerald-600">Mid (Fair)</span>
                  <span className="text-xs font-extrabold text-emerald-700">${projection.quantiles.mid}</span>
                </div>
                <div>
                  <span className="block text-[10px] uppercase font-bold text-slate-400">High</span>
                  <span className="text-xs font-bold text-slate-800">${projection.quantiles.high}</span>
                </div>
                <div>
                  <span className="block text-[10px] uppercase font-bold text-slate-400">Highest</span>
                  <span className="text-xs font-bold text-slate-800">${projection.quantiles.highest}</span>
                </div>
              </div>
            </div>

            {/* Monthly Delta Summary Footer */}
            <div className="mt-5 pt-3 border-t border-slate-100 flex items-center justify-between text-xs">
              <div className="flex items-center space-x-1.5 font-semibold text-slate-700">
                {projection.deltaPercentMonth < 0 ? (
                  <ArrowDownRight className="w-4 h-4 text-rose-600" />
                ) : (
                  <ArrowUpRight className="w-4 h-4 text-emerald-600" />
                )}
                <span>1-Month Projected Change:</span>
                <span className={projection.deltaPercentMonth < 0 ? 'text-rose-600 font-bold' : 'text-emerald-600 font-bold'}>
                  {projection.deltaPercentMonth > 0 ? `+${projection.deltaPercentMonth}%` : `${projection.deltaPercentMonth}%`}
                </span>
              </div>
              <span className="text-[11px] text-slate-400 font-medium">{projection.model ? 'ML model (experimental)' : 'Estimate'}</span>
            </div>

          </div>
        </div>
      </div>
    </div>
  );
};
