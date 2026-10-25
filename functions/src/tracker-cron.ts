import { onSchedule } from 'firebase-functions/v2/scheduler';
import * as admin from 'firebase-admin';
import { FieldValue } from 'firebase-admin/firestore';
import { sendPriceAlertEmail, renderAlertEmail, RETAILERS, Retailer, EmailStatus } from './email-service';
import { scrapeProduct } from '../../lib/context-dev';
import { getForecast } from './forecast';
import { isProductAlert, shouldTriggerAlert } from '../../lib/alert-rules';

if (!admin.apps.length) {
  admin.initializeApp();
}

const db = admin.firestore();

export interface AlertCheckResult {
  alertId: string;
  retailer: Retailer;
  productId: string;
  productTitle: string;
  currentPrice: number | null;
  targetPrice: number | null;
  triggered: boolean;
  email: EmailStatus | null;
  emailHtml?: string;
  note?: string;
}

// Price-checks one product alert, and emails and marks it triggered when its threshold is met
export async function checkAlert(doc: admin.firestore.DocumentSnapshot, retailer: Retailer): Promise<AlertCheckResult> {
  const alert = doc.data();
  const retailerName = RETAILERS[retailer].name;
  const productId = alert.targetValue;

  const result: AlertCheckResult = {
    alertId: doc.id,
    retailer,
    productId,
    productTitle: alert.productTitle || `${retailerName} Item (${productId})`,
    currentPrice: null,
    targetPrice: alert.targetPrice || null,
    triggered: false,
    email: null
  };

  // Extract latest price via context.dev
  let regularPrice = null;
  let category = null;
  try {
    const product = await scrapeProduct(RETAILERS[retailer].productUrl(productId), process.env.CONTEXT_DEV_API_KEY);
    if (product?.price) {
      result.currentPrice = product.price;
      regularPrice = product.regularPrice;
      category = product.category;
    }
  } catch (e) {
    console.warn(`[Cron] Price check failed for ${productId}:`, e);
  }

  // Never trigger on a price we could not read live
  if (!result.currentPrice) {
    result.note = 'Live price could not be read';
    return result;
  }

  // Check threshold trigger
  if (!shouldTriggerAlert(alert, result.currentPrice, regularPrice)) {
    result.note = 'Price is above the alert threshold';
    return result;
  }

  console.log(`[Cron] Trigger condition met for alert ${doc.id}!`);

  const projection = await getForecast({ currentPrice: result.currentPrice, category, retailer, title: result.productTitle, productId });
  const emailPayload = {
    to: alert.email,
    productTitle: result.productTitle,
    retailer,
    productId,
    currentPrice: result.currentPrice,
    originalPrice: alert.currentPriceAtCreation,
    projectedMid: projection.projectedMid,
    lowestPrice: projection.quantiles.lowest,
    recommendation: projection.recommendation,
    deltaPercentMonth: projection.deltaPercentMonth
  };

  result.email = await sendPriceAlertEmail(emailPayload);
  result.emailHtml = renderAlertEmail(emailPayload).html;

  // Mark as triggered so we don't spam
  await doc.ref.update({
    status: 'triggered',
    lastNotifiedAt: FieldValue.serverTimestamp()
  });
  result.triggered = true;

  return result;
}

// Runs the same check as the scheduled jobs for a single alert, on demand
export async function runAlertCheck(alertId: string): Promise<AlertCheckResult | { alertId: string; note: string }> {
  const doc = await db.collection('alerts').doc(alertId).get();

  if (!doc.exists) return { alertId, note: 'Alert not found' };

  const alert = doc.data();
  if (alert.status !== 'active') return { alertId, note: `Alert is ${alert.status}, not active` };
  if (!isProductAlert(alert)) return { alertId, note: 'Category and keyword alerts are not price-checked' };

  return checkAlert(doc, alert.retailer || 'amazon');
}

async function checkRetailerDeals(retailer: Retailer) {
  const retailerName = RETAILERS[retailer].name;
  console.log(`[Cron] Running ${retailerName} deal tracking sentinel...`);

  try {
    // Alerts created before Best Buy support carry no retailer field and are Amazon alerts
    const activeAlerts = db.collection('alerts').where('status', '==', 'active');
    const alertsSnapshot = await (retailer === 'amazon' ? activeAlerts : activeAlerts.where('retailer', '==', retailer))
      .limit(50)
      .get();

    if (alertsSnapshot.empty) {
      console.log('[Cron] No active alerts to process.');
      return;
    }

    for (const doc of alertsSnapshot.docs) {
      const alert = doc.data();
      if ((alert.retailer || 'amazon') !== retailer) continue;
      if (!isProductAlert(alert)) continue;

      await checkAlert(doc, retailer);
    }
  } catch (error) {
    console.error('[Cron] Error during scheduled execution:', error);
  }
}

export const checkAmazonDealsCron = onSchedule({ schedule: 'every 4 hours', timeoutSeconds: 1800 }, async (event) => {
  await checkRetailerDeals('amazon');
});

export const checkBestBuyDealsCron = onSchedule({ schedule: 'every 4 hours', timeoutSeconds: 1800 }, async (event) => {
  await checkRetailerDeals('bestbuy');
});
