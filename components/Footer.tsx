import React from 'react';
import { Sparkles } from 'lucide-react';

export const Footer: React.FC = () => {
  return (
    <footer className="w-full border-t border-slate-200 bg-white py-12">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 flex flex-col sm:flex-row items-center justify-between gap-6 text-xs text-slate-500">
        <div className="flex items-center space-x-2">
          <div className="w-6 h-6 rounded-lg bg-emerald-600 flex items-center justify-center text-white">
            <Sparkles className="w-3.5 h-3.5" />
          </div>
          <span className="font-bold text-slate-900">PriceOracle Alertant</span>
          <span>© {new Date().getFullYear()} — Predictive E-Commerce Intelligence</span>
        </div>

        <div className="flex items-center space-x-6">
          <span>Powered by context.dev</span>
          <span>Firebase Sentinel Worker</span>
          <span>Amazon Price Timing Engine</span>
        </div>
      </div>
    </footer>
  );
};
