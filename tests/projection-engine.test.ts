import { describe, it, expect } from 'vitest';
import { calculatePriceProjection } from '../lib/projection-engine';

describe('calculatePriceProjection', () => {
  it('matches the documented worked example with default inputs', () => {
    const result = calculatePriceProjection(189.99);

    expect(result.currentPrice).toBe(189.99);
    expect(result.projectedMid).toBe(189.51);
    expect(result.deltaPercentMonth).toBe(-0.3);
    expect(result.recommendation).toBe('FAIR PRICE');
    expect(result.quantiles).toEqual({
      lowest: 139.83,
      low: 164.91,
      mid: 189.51,
      high: 212.79,
      highest: 237.87
    });
  });

  it('outputs every field required by the README standard', () => {
    const result = calculatePriceProjection(100);

    expect(Object.keys(result.quantiles)).toEqual(['lowest', 'low', 'mid', 'high', 'highest']);
    expect(typeof result.deltaPercentMonth).toBe('number');
    expect(['BUY NOW', 'WAIT', 'FAIR PRICE']).toContain(result.recommendation);
    expect(result.quantiles.mid).toBe(result.projectedMid);
  });

  it('keeps the five tiers in ascending order for every category and price', () => {
    for (const category of ['Electronics', 'Gaming', 'Apparel', 'Home', 'Unknown']) {
      for (const price of [4.99, 79.99, 449, 1099]) {
        const { quantiles: q } = calculatePriceProjection(price, category);
        expect(q.lowest).toBeLessThan(q.low);
        expect(q.low).toBeLessThan(q.mid);
        expect(q.mid).toBeLessThan(q.high);
        expect(q.high).toBeLessThan(q.highest);
      }
    }
  });

  it('uses the volatility benchmark of each category', () => {
    // high = price * (1 + volatility)
    expect(calculatePriceProjection(100, 'Electronics').quantiles.high).toBe(112);
    expect(calculatePriceProjection(100, 'Gaming').quantiles.high).toBe(114);
    expect(calculatePriceProjection(100, 'Apparel').quantiles.high).toBe(122);
    expect(calculatePriceProjection(100, 'Home').quantiles.high).toBe(108);
  });

  it('falls back to the default volatility for categories it does not know', () => {
    expect(calculatePriceProjection(100, 'Home & Kitchen').quantiles.high).toBe(110);
    expect(calculatePriceProjection(100, 'Toys').quantiles.high).toBe(110);
  });

  it('recommends WAIT when the projected drop is 4% or more', () => {
    const result = calculatePriceProjection(100, 'Apparel', 0, 0.8);

    expect(result.deltaPercentMonth).toBe(-4.4);
    expect(result.recommendation).toBe('WAIT');
    expect(result.subtitle).toContain('4.4%');
  });

  it('recommends BUY NOW when the projected rise is 4% or more', () => {
    const result = calculatePriceProjection(100, 'Apparel', 0, 1.2);

    expect(result.deltaPercentMonth).toBe(4.4);
    expect(result.recommendation).toBe('BUY NOW');
  });

  it('recommends FAIR PRICE just inside the 4% thresholds', () => {
    expect(calculatePriceProjection(100, 'Apparel', 0, 0.83).recommendation).toBe('FAIR PRICE');
    expect(calculatePriceProjection(100, 'Apparel', 0, 1.17).recommendation).toBe('FAIR PRICE');
  });

  it('projects no movement when sentiment is neutral or a discount is certain', () => {
    expect(calculatePriceProjection(250, 'Gaming', 0.5, 1.0).deltaPercentMonth).toBe(0);
    expect(calculatePriceProjection(250, 'Gaming', 1, 0.5).projectedMid).toBe(250);
  });

  it('is deterministic, so server and client renders agree', () => {
    expect(calculatePriceProjection(398, 'Electronics', 0.92, 0.84)).toEqual(
      calculatePriceProjection(398, 'Electronics', 0.92, 0.84)
    );
  });

  it('derives confidence from the discount probability, between 75 and 90', () => {
    expect(calculatePriceProjection(100, 'Electronics', 0).confidence).toBe(75);
    expect(calculatePriceProjection(100, 'Electronics', 0.5).confidence).toBe(83);
    expect(calculatePriceProjection(100, 'Electronics', 1).confidence).toBe(90);
  });
});
