import { calculatePriceProjection, ProjectionResult } from '../../lib/projection-engine';

export interface ForecastInput {
  currentPrice: number;
  category?: string | null;
  retailer?: string | null;
  title?: string | null;
  productId?: string | null;
}

// 30-day price forecast from the ML model (the forecastPrice Python function in
// predictive-pricing-engine/). Falls back to the fixed formula, with model: null,
// when the model cannot be reached.
export async function getForecast(input: ForecastInput): Promise<ProjectionResult> {
  const url = process.env.ML_FORECAST_URL || `https://us-central1-${process.env.GCLOUD_PROJECT}.cloudfunctions.net/forecastPrice`;

  try {
    const res = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      // The list price is withheld: the model was trained on simulated data in which a price
      // below list always climbs back to it, so sending it would recommend BUY NOW for
      // every discounted product.
      body: JSON.stringify({ ...input, regularPrice: null }),
      signal: AbortSignal.timeout(8000)
    });

    if (res.ok) {
      const data = await res.json();
      if (data?.forecast) return data.forecast;
    }
  } catch (e) {
    console.warn('[Forecast] Model unavailable, using the formula:', e);
  }

  return { ...calculatePriceProjection(input.currentPrice, input.category || 'Electronics'), model: null };
}
