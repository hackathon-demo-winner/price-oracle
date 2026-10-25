export interface ProjectionResult {
  currentPrice: number;
  projectedMid: number;
  deltaPercentMonth: number;
  recommendation: 'BUY NOW' | 'WAIT' | 'FAIR PRICE';
  subtitle: string;
  confidence: number | null; // null from the ML model, which has no honest basis for one
  quantiles: {
    lowest: number;   // P5
    low: number;      // P25
    mid: number;      // P50
    high: number;     // P75
    highest: number;  // P95
  };
  // Set when the forecast came from the ML model; null or absent for the fixed formula
  model?: {
    name: string;
    version: string;
    trainedOn: 'synthetic' | 'real';
    [key: string]: unknown;
  } | null;
}

export function calculatePriceProjection(
  currentPrice: number,
  category = 'Electronics',
  discountProbability = 0.65,
  sentimentMultiplier = 0.94
): ProjectionResult {
  // Category volatility benchmarks
  const categoryVolatility: Record<string, number> = {
    Electronics: 0.12,
    Gaming: 0.14,
    Apparel: 0.22,
    Home: 0.08,
    Default: 0.10
  };

  const volatility = categoryVolatility[category] || categoryVolatility.Default;

  // Expected 30-day clearing price
  const projectedMid = Number(
    (currentPrice * (1 + (sentimentMultiplier - 1.0) * volatility * (1 - discountProbability))).toFixed(2)
  );

  const deltaPercentMonth = Number((((projectedMid - currentPrice) / currentPrice) * 100).toFixed(1));

  const lowest = Number((currentPrice * (1 - 2.2 * volatility)).toFixed(2));
  const low = Number((currentPrice * (1 - 1.1 * volatility)).toFixed(2));
  const mid = projectedMid;
  const high = Number((currentPrice * (1 + 1.0 * volatility)).toFixed(2));
  const highest = Number((currentPrice * (1 + 2.1 * volatility)).toFixed(2));

  let recommendation: 'BUY NOW' | 'WAIT' | 'FAIR PRICE';
  let subtitle = '';

  if (deltaPercentMonth <= -4.0) {
    recommendation = 'WAIT';
    subtitle = `Expected to drop ~${Math.abs(deltaPercentMonth)}% in 30 days`;
  } else if (deltaPercentMonth >= 4.0) {
    recommendation = 'BUY NOW';
    subtitle = `Expected to rise ~${deltaPercentMonth}% soon`;
  } else {
    recommendation = 'FAIR PRICE';
    subtitle = 'Currently trading near 30-day fair value';
  }

  // Deterministic so server-rendered and client-rendered values match
  const confidence = Math.round(75 + discountProbability * 15);

  return {
    currentPrice,
    projectedMid,
    deltaPercentMonth,
    recommendation,
    subtitle,
    confidence,
    quantiles: {
      lowest,
      low,
      mid,
      high,
      highest
    }
  };
}
