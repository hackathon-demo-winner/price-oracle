import { onRequest } from 'firebase-functions/v2/https';
import * as admin from 'firebase-admin';
import { extractAmazonContext, extractBestBuyContext, searchRetailers, researchProduct } from '../../lib/context-dev';
import { getForecast } from './forecast';
import { runAlertCheck } from './tracker-cron';
import { sendCategoryAlertNotice, sendAlertConfirmationEmail, RETAILERS } from './email-service';

if (!admin.apps.length) {
  admin.initializeApp();
}

const db = admin.firestore();

// The signed-in user's uid, proven by their Firebase ID token, or null
async function getVerifiedUid(req: { get(name: string): string | undefined }): Promise<string | null> {
  const header = req.get('Authorization') || '';
  if (!header.startsWith('Bearer ')) return null;

  try {
    return (await admin.auth().verifyIdToken(header.slice('Bearer '.length))).uid;
  } catch {
    return null;
  }
}

export const createAlert = onRequest(async (req, res) => {
  if (req.method !== 'POST') {
    res.status(405).json({ error: 'Method not allowed' });
    return;
  }

  try {
    const {
      email,
      targetType, // 'asin' | 'sku' | 'category' | 'keyword'
      targetValue, // URL, ASIN, Best Buy SKU, or category name
      targetPrice,
      minDiscountPercent,
      triggerOnPredictedDrop = true,
    } = req.body || {};

    if (!email || !targetValue) {
      res.status(400).json({ error: 'Email and target product or category required' });
      return;
    }

    // Extract product details if ASIN, SKU, or URL
    let productDetails = null;
    let productId = null;
    let retailer = null;
    let initialProjection = null;

    // Best Buy SKUs are 7-8 digits, so a bare one can never be mistaken for a 10-character ASIN
    const isBestBuy = targetType === 'sku' || targetValue.includes('bestbuy.com') || /^\d{7,8}$/.test(targetValue.trim());

    const isAmazon = !isBestBuy && (targetType === 'asin' || targetValue.includes('amazon.com'));

    if (isBestBuy) {
      const bestBuyDetails = await extractBestBuyContext(targetValue);
      productDetails = bestBuyDetails;
      productId = bestBuyDetails?.sku;
      retailer = 'bestbuy';
    } else if (isAmazon) {
      const amazonDetails = await extractAmazonContext(targetValue);
      productDetails = amazonDetails;
      productId = amazonDetails?.asin;
      retailer = 'amazon';
    }

    if ((isBestBuy || isAmazon) && !productDetails) {
      res.status(422).json({ success: false, error: 'Could not read a live price for that product. Check the link, ASIN, or SKU and try again.' });
      return;
    }

    if (productDetails) {
      initialProjection = await getForecast({
        currentPrice: productDetails.currentPrice,
        category: productDetails.category,
        retailer,
        title: productDetails.title,
        productId
      });
    }

    const alertDoc = {
      email: email.toLowerCase().trim(),
      userId: await getVerifiedUid(req),
      targetType: isBestBuy ? 'sku' : targetType || (targetValue.includes('amazon.com') ? 'asin' : 'category'),
      targetValue: productId || targetValue,
      retailer,
      productUrl: retailer && productId ? RETAILERS[retailer].productUrl(productId) : null,
      productTitle: productDetails?.title || `Alert on ${targetValue}`,
      currentPriceAtCreation: productDetails?.currentPrice || null,
      targetPrice: targetPrice ? Number(targetPrice) : null,
      minDiscountPercent: minDiscountPercent ? Number(minDiscountPercent) : null,
      triggerOnPredictedDrop,
      initialProjection: initialProjection || null,
      status: 'active',
      createdAt: new Date().toISOString(),
    };

    const docRef = await db.collection('alerts').add(alertDoc);

    // Category and keyword alerts are not price-checked yet; say so instead of going silent
    // A product alert gets a confirmation of what is being watched and when it will fire
    const notice = productDetails
      ? await sendAlertConfirmationEmail({
          to: alertDoc.email,
          productTitle: alertDoc.productTitle,
          retailer,
          productId,
          currentPrice: productDetails.currentPrice,
          targetPrice: alertDoc.targetPrice,
          minDiscountPercent: alertDoc.minDiscountPercent,
          recommendation: initialProjection?.recommendation,
          rangeLow: initialProjection?.quantiles?.low,
          rangeHigh: initialProjection?.quantiles?.high
        })
      : await sendCategoryAlertNotice(alertDoc.email, String(targetValue));

    res.json({ success: true, alertId: docRef.id, alert: alertDoc, notice });
  } catch (err: any) {
    console.error('Alert creation error:', err);
    res.status(500).json({ success: false, error: err.message });
  }
});

export const subscribe = onRequest(async (req, res) => {
  if (req.method !== 'POST') {
    res.status(405).json({ error: 'Method not allowed' });
    return;
  }

  try {
    const { email, source = 'landing_hero' } = req.body || {};

    if (!email || !email.includes('@')) {
      res.status(400).json({ error: 'Valid email required' });
      return;
    }

    const subscriberData = {
      email: email.toLowerCase().trim(),
      source,
      createdAt: new Date().toISOString(),
      active: true,
    };

    await db.collection('subscribers').doc(email.toLowerCase().trim()).set(subscriberData, { merge: true });

    res.json({ success: true, message: 'Subscribed to hot deal alerts!' });
  } catch (err: any) {
    console.error('Subscription error:', err);
    res.status(500).json({ success: false, error: err.message });
  }
});

// Runs the scheduled price check for one alert right now. Disabled unless DEMO_TOKEN is set,
// and every call must present it, because each check spends context.dev credits.
export const runAlertCheckNow = onRequest({ timeoutSeconds: 300 }, async (req, res) => {
  const token = process.env.DEMO_TOKEN;
  if (!token || req.get('x-demo-token') !== token) {
    res.status(404).json({ error: 'Not found' });
    return;
  }

  if (req.method !== 'POST') {
    res.status(405).json({ error: 'Method not allowed' });
    return;
  }

  try {
    const { alertId } = req.body || {};

    if (!alertId) {
      res.status(400).json({ error: 'alertId required' });
      return;
    }

    res.json({ success: true, result: await runAlertCheck(alertId) });
  } catch (err: any) {
    console.error('Alert check error:', err);
    res.status(500).json({ success: false, error: err.message });
  }
});

// Live product search across Amazon and Best Buy. Responses are cached at the CDN,
// because every uncached search spends a context.dev credit.
export const searchLive = onRequest(async (req, res) => {
  try {
    const query = String(req.query.q || '').trim();

    if (query.length < 3 || query.length > 120) {
      res.status(400).json({ error: 'Search query must be between 3 and 120 characters' });
      return;
    }

    const results = await searchRetailers(query, process.env.CONTEXT_DEV_API_KEY);

    res.set('Cache-Control', 'public, max-age=600, s-maxage=3600');
    res.json({ success: true, results });
  } catch (err: any) {
    console.error('Search error:', err);
    res.status(500).json({ success: false, error: err.message });
  }
});

// The signed-in user's own alerts: GET lists them, POST updates or deletes one
export const myAlerts = onRequest(async (req, res) => {
  res.set('Cache-Control', 'private, no-store');

  try {
    const uid = await getVerifiedUid(req);
    if (!uid) {
      res.status(401).json({ error: 'Sign in required' });
      return;
    }

    if (req.method === 'GET') {
      const snapshot = await db.collection('alerts').where('userId', '==', uid).limit(200).get();
      const alerts = snapshot.docs
        .map(doc => {
          const { email, userId, lastNotifiedAt, ...alert } = doc.data();
          return { id: doc.id, ...alert };
        })
        .sort((a: any, b: any) => String(b.createdAt).localeCompare(String(a.createdAt)));

      res.json({ success: true, alerts });
      return;
    }

    if (req.method !== 'POST') {
      res.status(405).json({ error: 'Method not allowed' });
      return;
    }

    const { action, alertId, targetPrice, status, productTitle } = req.body || {};
    const ref = db.collection('alerts').doc(String(alertId || '_'));
    const doc = await ref.get();

    if (!doc.exists || doc.data().userId !== uid) {
      res.status(404).json({ error: 'Alert not found' });
      return;
    }

    if (action === 'delete') {
      await ref.delete();
      res.json({ success: true });
      return;
    }

    if (action !== 'update') {
      res.status(400).json({ error: 'Unknown action' });
      return;
    }

    const changes: Record<string, unknown> = {};
    if (targetPrice !== undefined) changes.targetPrice = targetPrice ? Number(targetPrice) : null;
    if (status === 'active' || status === 'paused') changes.status = status;
    if (typeof productTitle === 'string' && productTitle.trim()) changes.productTitle = productTitle.trim().slice(0, 200);

    await ref.update(changes);
    res.json({ success: true });
  } catch (err: any) {
    console.error('My alerts error:', err);
    res.status(500).json({ success: false, error: err.message });
  }
});

// Market check by a research agent for one product. Cached for a day at the CDN,
// because each uncached run spends about 10 context.dev credits.
export const productInsight = onRequest({ timeoutSeconds: 60 }, async (req, res) => {
  try {
    const title = String(req.query.q || '').trim();

    if (title.length < 5 || title.length > 200) {
      res.status(400).json({ error: 'Product name must be between 5 and 200 characters' });
      return;
    }

    const insight = await researchProduct(title, process.env.CONTEXT_DEV_API_KEY);

    if (!insight) {
      res.status(502).json({ success: false, error: 'The market check did not return an answer. Try again.' });
      return;
    }

    res.set('Cache-Control', 'public, max-age=3600, s-maxage=86400');
    res.json({ success: true, insight });
  } catch (err: any) {
    console.error('Insight error:', err);
    res.status(500).json({ success: false, error: err.message });
  }
});
