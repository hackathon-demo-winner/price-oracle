export type Retailer = 'amazon' | 'bestbuy';

export const RETAILERS: Record<Retailer, { name: string; productUrl: (productId: string) => string }> = {
  amazon: {
    name: 'Amazon',
    productUrl: (asin) => `https://www.amazon.com/dp/${asin}`
  },
  bestbuy: {
    name: 'Best Buy',
    productUrl: (sku) => `https://www.bestbuy.com/site/${sku}.p?skuId=${sku}`
  }
};

interface LinkableAlert {
  productUrl?: string | null;
  retailer?: string | null;
  targetType?: string;
  targetValue?: string;
}

function alertRetailer(alert: LinkableAlert): Retailer | null {
  if (alert.retailer === 'amazon' || alert.retailer === 'bestbuy') return alert.retailer;

  // Alerts saved before the retailer was recorded: a Best Buy SKU is 7-8 digits, an ASIN is 10 characters
  const value = (alert.targetValue || '').trim();
  if (/^\d{7,8}$/.test(value)) return 'bestbuy';
  if (/^[A-Z0-9]{10}$/i.test(value)) return 'amazon';
  return null;
}

// The product page for an alert, or null for category and keyword alerts
export function alertProductLink(alert: LinkableAlert): { url: string; retailerName: string } | null {
  if (alert.targetType === 'category' || alert.targetType === 'keyword') return null;

  const retailer = alertRetailer(alert);
  const value = (alert.targetValue || '').trim();
  const url = alert.productUrl || (/^https?:\/\//i.test(value) ? value : retailer ? RETAILERS[retailer].productUrl(value) : null);
  if (!url) return null;

  const retailerName = retailer ? RETAILERS[retailer].name : url.includes('bestbuy.com') ? 'Best Buy' : url.includes('amazon.com') ? 'Amazon' : 'the retailer';
  return { url, retailerName };
}
