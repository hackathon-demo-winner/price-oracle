import { describe, it, expect } from 'vitest';
import { alertProductLink } from '../lib/retailers';

describe('alertProductLink', () => {
  it('uses the saved product link when there is one', () => {
    expect(alertProductLink({ productUrl: 'https://www.amazon.com/dp/B0CHWRXH8B', retailer: 'amazon', targetType: 'asin', targetValue: 'B0CHWRXH8B' })).toEqual({
      url: 'https://www.amazon.com/dp/B0CHWRXH8B',
      retailerName: 'Amazon'
    });
  });

  it('builds the link from the retailer and product ID for alerts saved without one', () => {
    expect(alertProductLink({ retailer: 'bestbuy', targetType: 'sku', targetValue: '6447382' })).toEqual({
      url: 'https://www.bestbuy.com/site/6447382.p?skuId=6447382',
      retailerName: 'Best Buy'
    });
  });

  it('works out the retailer for old alerts that only stored an ASIN or SKU', () => {
    expect(alertProductLink({ targetType: 'asin', targetValue: 'B0CX22ZW1T' })?.url).toBe('https://www.amazon.com/dp/B0CX22ZW1T');
    expect(alertProductLink({ targetType: 'asin', targetValue: '6505727' })?.url).toBe('https://www.bestbuy.com/site/6505727.p?skuId=6505727');
  });

  it('keeps a full URL that was stored as the target', () => {
    expect(alertProductLink({ targetType: 'asin', targetValue: 'https://www.amazon.com/dp/B0CHWRXH8B?th=1' })).toEqual({
      url: 'https://www.amazon.com/dp/B0CHWRXH8B?th=1',
      retailerName: 'Amazon'
    });
  });

  it('has no link for category and keyword alerts, or an unrecognised target', () => {
    expect(alertProductLink({ targetType: 'category', targetValue: 'Gaming' })).toBeNull();
    expect(alertProductLink({ targetType: 'keyword', targetValue: 'hair pin' })).toBeNull();
    expect(alertProductLink({ targetType: 'asin', targetValue: 'not a product' })).toBeNull();
  });
});
