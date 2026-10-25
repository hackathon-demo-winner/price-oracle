export interface ExtractedProductContext {
  asin: string;
  title: string;
  currentPrice: number;
  originalPrice?: number;
  category: string;
  historicalMin?: number;
  historicalMax?: number;
  isAvailable: boolean;
}

// Product fields returned by context.dev's scrape endpoint (subset we use)
export interface ScrapedProduct {
  name: string;
  price: number | null;
  regularPrice: number | null;
  category: string | null;
}

export async function scrapeProduct(url: string, apiKey: string): Promise<ScrapedProduct | null> {
  // context.dev limits how many requests run at once, so wait and retry when it answers 429
  for (let attempt = 0; ; attempt++) {
    const res = await fetch('https://api.context.dev/v1/web/scrape', {
      method: 'POST',
      headers: {
        'Authorization': `Bearer ${apiKey}`,
        'Content-Type': 'application/json'
      },
      body: JSON.stringify({
        url,
        formats: { product: true }
      })
    });

    if (res.status === 429 && attempt < 3) {
      const retryAfterSeconds = Number(res.headers.get('Retry-After')) || 2;
      await new Promise(resolve => setTimeout(resolve, Math.min(retryAfterSeconds, 10) * 1000));
      continue;
    }

    if (!res.ok) return null;

    const data = await res.json();
    return data?.product?.data?.product || null;
  }
}

// Returns null when no ASIN is found or the live price cannot be read
export async function extractAmazonContext(urlOrAsin: string): Promise<ExtractedProductContext | null> {
  const apiKey = process.env.CONTEXT_DEV_API_KEY || '';
  
  // Extract ASIN from string if URL
  const asinMatch = urlOrAsin.match(/(?:dp|gp\/product)\/([A-Z0-9]{10})/i) || urlOrAsin.match(/^[A-Z0-9]{10}$/i);
  if (!asinMatch) return null;
  const asin = asinMatch[1] || asinMatch[0];
  const url = `https://www.amazon.com/dp/${asin}`;

  try {
    const product = await scrapeProduct(url, apiKey);

    if (product?.price) {
      return {
        asin,
        title: product.name || 'Amazon Product',
        currentPrice: product.price,
        originalPrice: product.regularPrice || undefined,
        category: product.category || 'Electronics',
        isAvailable: true
      };
    }
  } catch (err) {
    console.warn('[context.dev] Product lookup failed', err);
  }

  return null;
}

export interface ExtractedBestBuyContext extends Omit<ExtractedProductContext, 'asin'> {
  sku: string;
}

// Returns null when no SKU is found or the live price cannot be read
export async function extractBestBuyContext(urlOrSku: string): Promise<ExtractedBestBuyContext | null> {
  const apiKey = process.env.CONTEXT_DEV_API_KEY;

  // Extract SKU from string if URL
  const skuMatch = urlOrSku.match(/(?:skuId=|\/sku\/|\/)(\d{7,8})(?:\.p)?(?:[/?&#]|$)/i) || urlOrSku.trim().match(/^\d{7,8}$/);
  if (!skuMatch) return null;
  const sku = skuMatch[1] || skuMatch[0];
  const url = `https://www.bestbuy.com/site/${sku}.p?skuId=${sku}`;

  try {
    const product = await scrapeProduct(url, apiKey);

    if (product?.price) {
      return {
        sku,
        title: product.name || 'Best Buy Product',
        currentPrice: product.price,
        originalPrice: product.regularPrice || undefined,
        category: product.category || 'Electronics',
        isAvailable: true
      };
    }
  } catch (err) {
    console.warn('[context.dev] Product lookup failed', err);
  }

  return null;
}

export interface RetailSearchResult {
  retailer: 'amazon' | 'bestbuy';
  productId: string; // Amazon ASIN or Best Buy SKU
  title: string;
  description: string;
  url: string;
}

// Maps a search result URL to a product we can price-check, or null when it is not a product page
export function parseRetailProductUrl(url: string): Pick<RetailSearchResult, 'retailer' | 'productId' | 'url'> | null {
  if (/(^|[/.])amazon[.]com[/]/i.test(url)) {
    // Amazon search results use /dp/, /gp/product/ and /clp/ paths, all followed by the ASIN
    const asinMatch = url.match(/[/](?:dp|clp|gp[/]product)[/]([A-Z0-9]{10})(?:[/?#]|$)/i);
    if (!asinMatch) return null;
    const asin = asinMatch[1].toUpperCase();
    return { retailer: 'amazon', productId: asin, url: `https://www.amazon.com/dp/${asin}` };
  }

  if (/(^|[/.])bestbuy[.]com[/]/i.test(url)) {
    const skuMatch = url.match(/(?:skuId=|[/]sku[/]|[/])([0-9]{7,8})(?:[.]p)?(?:[/?&#]|$)/i);
    if (!skuMatch) return null;
    return { retailer: 'bestbuy', productId: skuMatch[1], url: `https://www.bestbuy.com/site/${skuMatch[1]}.p?skuId=${skuMatch[1]}` };
  }

  return null;
}

// Finds real Amazon and Best Buy product pages for a shopper's query via context.dev web search
export async function searchRetailers(query: string, apiKey: string): Promise<RetailSearchResult[]> {
  for (let attempt = 0; ; attempt++) {
    const res = await fetch('https://api.context.dev/v1/web/search', {
      method: 'POST',
      headers: {
        'Authorization': `Bearer ${apiKey}`,
        'Content-Type': 'application/json'
      },
      body: JSON.stringify({
        query,
        includeDomains: ['amazon.com', 'bestbuy.com'],
        numResults: 10,
        country: 'us'
      })
    });

    if (res.status === 429 && attempt < 3) {
      const retryAfterSeconds = Number(res.headers.get('Retry-After')) || 2;
      await new Promise(resolve => setTimeout(resolve, Math.min(retryAfterSeconds, 10) * 1000));
      continue;
    }

    if (!res.ok) return [];

    const data = await res.json();
    const seen = new Set<string>();
    const results: RetailSearchResult[] = [];

    for (const item of data?.results || []) {
      const product = parseRetailProductUrl(item.url || '');
      if (!product || seen.has(product.retailer + product.productId)) continue;
      seen.add(product.retailer + product.productId);

      results.push({
        ...product,
        title: String(item.title || '').replace(/^Amazon[.]com: /i, '').replace(/ [-|:] (Amazon[.]com|Best Buy)$/i, '').trim(),
        description: item.description || ''
      });
    }

    return results;
  }
}

export interface ProductInsight {
  outlook: 'drop' | 'rise' | 'stable' | 'unknown';
  summary: string;
  keyFactors: string[];
  sources: string[];
}

// Asks context.dev's research agent whether a product's price is likely to move in the next
// 30 days. The agent searches and reads the web itself; this costs about 10 credits a call.
export async function researchProduct(title: string, apiKey: string): Promise<ProductInsight | null> {
  const res = await fetch('https://api.context.dev/v1/web/answers', {
    method: 'POST',
    headers: {
      'Authorization': `Bearer ${apiKey}`,
      'Content-Type': 'application/json'
    },
    body: JSON.stringify({
      mode: 'fast',
      task: `For a shopper deciding whether to buy "${title}" now or wait: is its US retail price likely to drop, rise, or stay stable over the next 30 days? Consider upcoming sale events, a newer replacement model, and recent price trends.`,
      json_format: {
        outlook: 'drop | rise | stable | unknown',
        summary: 'two plain sentences for a shopper',
        keyFactors: ['short factor']
      },
      timeoutOpts: { milliseconds: 45000, behavior: 'return-partial' }
    })
  });

  if (!res.ok) return null;

  const data = await res.json();
  const answer = data?.json_content;
  if (!answer?.summary) return null;

  const outlookText = String(answer.outlook || '').toLowerCase();
  const outlook = (['drop', 'rise', 'stable'] as const).find(o => outlookText.includes(o)) || 'unknown';

  return {
    outlook,
    summary: String(answer.summary),
    keyFactors: Array.isArray(answer.keyFactors) ? answer.keyFactors.map(String).slice(0, 5) : [],
    sources: Array.isArray(data.sources) ? data.sources.map(String).slice(0, 5) : []
  };
}
