export interface AlertThresholds {
  targetType?: string;
  targetPrice?: number | null;
  minDiscountPercent?: number | null;
  currentPriceAtCreation?: number | null;
}

// Alerts on a category or keyword have no single product page to price-check
export function isProductAlert(alert: AlertThresholds): boolean {
  return alert.targetType !== 'category' && alert.targetType !== 'keyword';
}

export function shouldTriggerAlert(
  alert: AlertThresholds,
  currentPrice: number,
  regularPrice?: number | null
): boolean {
  if (alert.targetPrice && currentPrice <= alert.targetPrice) return true;

  // Discount is measured against the live list price, or the price at creation when there is none
  const referencePrice = regularPrice || alert.currentPriceAtCreation;
  if (alert.minDiscountPercent && referencePrice) {
    const discountPercent = ((referencePrice - currentPrice) / referencePrice) * 100;
    if (discountPercent >= alert.minDiscountPercent) return true;
  }

  // No explicit threshold: notify on a 5% dip from the price at creation
  if (!alert.targetPrice && !alert.minDiscountPercent && alert.currentPriceAtCreation) {
    return currentPrice <= alert.currentPriceAtCreation * 0.95;
  }

  return false;
}
