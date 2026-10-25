import { describe, it, expect } from 'vitest';
import { isProductAlert, shouldTriggerAlert } from '../lib/alert-rules';

describe('isProductAlert', () => {
  it('is true for ASIN and SKU alerts, and for old alerts with no type', () => {
    expect(isProductAlert({ targetType: 'asin' })).toBe(true);
    expect(isProductAlert({ targetType: 'sku' })).toBe(true);
    expect(isProductAlert({})).toBe(true);
  });

  it('is false for category and keyword alerts', () => {
    expect(isProductAlert({ targetType: 'category' })).toBe(false);
    expect(isProductAlert({ targetType: 'keyword' })).toBe(false);
  });
});

describe('shouldTriggerAlert', () => {
  it('triggers when the price is at or below the target price', () => {
    const alert = { targetPrice: 175, currentPriceAtCreation: 200 };

    expect(shouldTriggerAlert(alert, 175)).toBe(true);
    expect(shouldTriggerAlert(alert, 149.99)).toBe(true);
    expect(shouldTriggerAlert(alert, 175.01)).toBe(false);
  });

  it('does not fall back to the 5% rule when a target price is set', () => {
    // 185 is more than 5% below 200, but the user asked for 175
    expect(shouldTriggerAlert({ targetPrice: 175, currentPriceAtCreation: 200 }, 185)).toBe(false);
  });

  it('with no thresholds, triggers on a 5% dip from the price at creation', () => {
    const alert = { currentPriceAtCreation: 200 };

    expect(shouldTriggerAlert(alert, 190)).toBe(true);
    expect(shouldTriggerAlert(alert, 190.01)).toBe(false);
    expect(shouldTriggerAlert(alert, 200)).toBe(false);
  });

  it('never triggers when there is nothing to compare against', () => {
    expect(shouldTriggerAlert({}, 1)).toBe(false);
    expect(shouldTriggerAlert({ targetPrice: null, currentPriceAtCreation: null }, 1)).toBe(false);
  });

  it('triggers on the minimum discount against the live list price', () => {
    const alert = { minDiscountPercent: 20, currentPriceAtCreation: 90 };

    expect(shouldTriggerAlert(alert, 80, 100)).toBe(true);
    expect(shouldTriggerAlert(alert, 80.01, 100)).toBe(false);
  });

  it('measures the minimum discount against the creation price when there is no list price', () => {
    const alert = { minDiscountPercent: 10, currentPriceAtCreation: 200 };

    expect(shouldTriggerAlert(alert, 180)).toBe(true);
    expect(shouldTriggerAlert(alert, 185, null)).toBe(false);
  });

  it('triggers when either the target price or the minimum discount is met', () => {
    const alert = { targetPrice: 50, minDiscountPercent: 20, currentPriceAtCreation: 100 };

    expect(shouldTriggerAlert(alert, 50, 55)).toBe(true);
    expect(shouldTriggerAlert(alert, 80, 100)).toBe(true);
    expect(shouldTriggerAlert(alert, 90, 100)).toBe(false);
  });
});
