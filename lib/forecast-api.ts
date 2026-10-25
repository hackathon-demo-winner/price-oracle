'use client';

import { useEffect, useState } from 'react';
import type { ProjectionResult } from './projection-engine';

export interface ForecastRequest {
  currentPrice: number;
  category?: string | null;
  retailer?: string | null;
  title?: string | null;
}

// 30-day forecast from the ML model, or null when it cannot be reached
export async function fetchForecast(request: ForecastRequest): Promise<ProjectionResult | null> {
  try {
    const res = await fetch('/api/forecast', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      // regularPrice is withheld on purpose; see functions/src/forecast.ts
      body: JSON.stringify({ ...request, regularPrice: null })
    });
    if (!res.ok) return null;
    return (await res.json()).forecast || null;
  } catch {
    return null;
  }
}

// "+1.2% expected in 30 days" / "No change expected in 30 days"
export function describeForecast(forecast: ProjectionResult): string {
  const delta = forecast.deltaPercentMonth;
  if (!delta) return 'No change expected in 30 days';
  return `${delta > 0 ? '+' : ''}${delta}% expected in 30 days`;
}

interface ForecastableProduct {
  id: string;
  price: number;
  category: string;
  title: string;
  retailer?: string;
}

// Model forecasts for a list of products, keyed by product id. Products without a price are skipped.
export function useForecasts(products: ForecastableProduct[]): Record<string, ProjectionResult> {
  const [forecasts, setForecasts] = useState<Record<string, ProjectionResult>>({});

  useEffect(() => {
    let cancelled = false;

    products
      .filter(p => p.price > 0 && !forecasts[p.id])
      .forEach(async p => {
        const forecast = await fetchForecast({
          currentPrice: p.price,
          category: p.category,
          retailer: p.retailer === 'Best Buy' ? 'bestbuy' : 'amazon',
          title: p.title
        });
        if (forecast && !cancelled) setForecasts(current => ({ ...current, [p.id]: forecast }));
      });

    return () => {
      cancelled = true;
    };
    // Re-run only when the set of products changes
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [products.map(p => p.id).join('|')]);

  return forecasts;
}
