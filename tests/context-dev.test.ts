import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { extractAmazonContext, extractBestBuyContext, scrapeProduct, parseRetailProductUrl, searchRetailers } from '../lib/context-dev';

const scrapeResponse = (product: unknown, ok = true) => ({
  ok,
  status: ok ? 200 : 500,
  headers: { get: () => null },
  json: async () => ({ product: { requested: true, success: true, data: { isProductPage: !!product, product } } })
});

let fetchMock: ReturnType<typeof vi.fn>;

beforeEach(() => {
  fetchMock = vi.fn();
  vi.stubGlobal('fetch', fetchMock);
  vi.stubEnv('CONTEXT_DEV_API_KEY', 'test-key');
  vi.spyOn(console, 'warn').mockImplementation(() => {});
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
  vi.restoreAllMocks();
});

const requestedUrl = () => JSON.parse(fetchMock.mock.calls[0][1].body).url;

describe('scrapeProduct', () => {
  it('asks the context.dev scrape endpoint for product output with the bearer key', async () => {
    fetchMock.mockResolvedValue(scrapeResponse({ name: 'Thing', price: 10, regularPrice: null, category: null }));

    await scrapeProduct('https://example.com/p', 'abc');

    const [endpoint, init] = fetchMock.mock.calls[0];
    expect(endpoint).toBe('https://api.context.dev/v1/web/scrape');
    expect(init.method).toBe('POST');
    expect(init.headers.Authorization).toBe('Bearer abc');
    expect(JSON.parse(init.body)).toEqual({ url: 'https://example.com/p', formats: { product: true } });
  });

  it('returns the nested product', async () => {
    const product = { name: 'Thing', price: 10, regularPrice: 12, category: 'Toys' };
    fetchMock.mockResolvedValue(scrapeResponse(product));

    expect(await scrapeProduct('https://example.com/p', 'abc')).toEqual(product);
  });

  it('returns null for an error response or a page that is not a product', async () => {
    fetchMock.mockResolvedValueOnce(scrapeResponse({ name: 'x', price: 1 }, false));
    expect(await scrapeProduct('https://example.com/p', 'abc')).toBeNull();

    fetchMock.mockResolvedValueOnce(scrapeResponse(null));
    expect(await scrapeProduct('https://example.com/p', 'abc')).toBeNull();
  });
});

describe('scrapeProduct rate limiting', () => {
  const busy = { ok: false, status: 429, headers: { get: () => '0.01' }, json: async () => ({}) };

  it('retries after a 429 and returns the product once the API is free', async () => {
    const product = { name: 'Thing', price: 10, regularPrice: null, category: null };
    fetchMock.mockResolvedValueOnce(busy).mockResolvedValueOnce(busy).mockResolvedValueOnce(scrapeResponse(product));

    expect(await scrapeProduct('https://example.com/p', 'abc')).toEqual(product);
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });

  it('gives up with null after three retries', async () => {
    fetchMock.mockResolvedValue(busy);

    expect(await scrapeProduct('https://example.com/p', 'abc')).toBeNull();
    expect(fetchMock).toHaveBeenCalledTimes(4);
  });
});

describe('extractAmazonContext', () => {
  const product = { name: 'Logitech Mouse', price: 84.99, regularPrice: 99.99, category: 'Computers' };

  it.each([
    ['https://www.amazon.com/dp/B09V3HN1KC', 'B09V3HN1KC'],
    ['https://www.amazon.com/Some-Product-Name/dp/B09V3HN1KC/ref=sr_1_1?keywords=mouse', 'B09V3HN1KC'],
    ['https://www.amazon.com/gp/product/B08N5WRWNW', 'B08N5WRWNW'],
    ['B08F6XQ1N8', 'B08F6XQ1N8'],
  ])('finds the ASIN in %s', async (input, asin) => {
    fetchMock.mockResolvedValue(scrapeResponse(product));

    const result = await extractAmazonContext(input);

    expect(result?.asin).toBe(asin);
    expect(requestedUrl()).toBe(`https://www.amazon.com/dp/${asin}`);
  });

  it('maps the live product onto the context', async () => {
    fetchMock.mockResolvedValue(scrapeResponse(product));

    expect(await extractAmazonContext('B09V3HN1KC')).toEqual({
      asin: 'B09V3HN1KC',
      title: 'Logitech Mouse',
      currentPrice: 84.99,
      originalPrice: 99.99,
      category: 'Computers',
      isAvailable: true
    });
  });

  it('defaults the category to Electronics when the page has none', async () => {
    fetchMock.mockResolvedValue(scrapeResponse({ ...product, category: null, regularPrice: null }));

    const result = await extractAmazonContext('B09V3HN1KC');

    expect(result?.category).toBe('Electronics');
    expect(result?.originalPrice).toBeUndefined();
  });

  it('returns null without calling the API when there is no ASIN', async () => {
    expect(await extractAmazonContext('not a product')).toBeNull();
    expect(await extractAmazonContext('https://www.amazon.com/s?k=mouse')).toBeNull();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('never invents a price: null when the lookup fails, errors, or has no price', async () => {
    fetchMock.mockResolvedValueOnce(scrapeResponse(product, false));
    expect(await extractAmazonContext('B09V3HN1KC')).toBeNull();

    fetchMock.mockRejectedValueOnce(new Error('network down'));
    expect(await extractAmazonContext('B09V3HN1KC')).toBeNull();

    fetchMock.mockResolvedValueOnce(scrapeResponse({ ...product, price: null }));
    expect(await extractAmazonContext('B09V3HN1KC')).toBeNull();
  });
});

describe('extractBestBuyContext', () => {
  const product = { name: 'Apple - AirPods Pro 2', price: 249.69, regularPrice: null, category: null };

  it.each([
    ['https://www.bestbuy.com/site/apple-airpods-pro/6447382.p?skuId=6447382', '6447382'],
    ['https://www.bestbuy.com/site/apple-airpods-pro/6447382.p', '6447382'],
    ['https://www.bestbuy.com/product/apple-airpods/J3ZYG2T5Q9/sku/6447382', '6447382'],
    ['https://www.bestbuy.com/product/x/J3ZYG2T5Q9/sku/6447382/openbox?condition=fair', '6447382'],
    [' 6505727 ', '6505727'],
    ['12734208', '12734208'],
  ])('finds the SKU in %s', async (input, sku) => {
    fetchMock.mockResolvedValue(scrapeResponse(product));

    const result = await extractBestBuyContext(input);

    expect(result?.sku).toBe(sku);
    expect(requestedUrl()).toBe(`https://www.bestbuy.com/site/${sku}.p?skuId=${sku}`);
  });

  it('maps the live product onto the context', async () => {
    fetchMock.mockResolvedValue(scrapeResponse(product));

    expect(await extractBestBuyContext('6447382')).toEqual({
      sku: '6447382',
      title: 'Apple - AirPods Pro 2',
      currentPrice: 249.69,
      originalPrice: undefined,
      category: 'Electronics',
      isAvailable: true
    });
  });

  it('sends the key from CONTEXT_DEV_API_KEY', async () => {
    fetchMock.mockResolvedValue(scrapeResponse(product));

    await extractBestBuyContext('6447382');

    expect(fetchMock.mock.calls[0][1].headers.Authorization).toBe('Bearer test-key');
  });

  it('returns null without calling the API when there is no SKU', async () => {
    expect(await extractBestBuyContext('B0CX23V258')).toBeNull();
    expect(await extractBestBuyContext('https://www.bestbuy.com/product/apple-airpods/J3ZYG2T5Q9')).toBeNull();
    expect(await extractBestBuyContext('https://www.bestbuy.com/site/promo/123456789012')).toBeNull();
    expect(await extractBestBuyContext('Electronics')).toBeNull();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('never invents a price: null when the lookup fails, errors, or has no price', async () => {
    fetchMock.mockResolvedValueOnce(scrapeResponse(product, false));
    expect(await extractBestBuyContext('6447382')).toBeNull();

    fetchMock.mockRejectedValueOnce(new Error('network down'));
    expect(await extractBestBuyContext('6447382')).toBeNull();

    fetchMock.mockResolvedValueOnce(scrapeResponse({ ...product, price: null }));
    expect(await extractBestBuyContext('6447382')).toBeNull();
  });
});

describe('parseRetailProductUrl', () => {
  it.each([
    ['https://www.amazon.com/G-SKILL-6000MT-Desktop-Computer/dp/B0DN43PTB9', 'B0DN43PTB9'],
    ['https://www.amazon.com/clp/B0C1TKSDKR', 'B0C1TKSDKR'],
    ['https://www.amazon.com/dp/B0CBL2YGYP?tag=synack-20&linkCode=osi&th=1&psc=1', 'B0CBL2YGYP'],
    ['https://www.amazon.com/gp/product/b08n5wrwnw/', 'B08N5WRWNW'],
  ])('reads the ASIN from %s and returns a clean product link', (url, asin) => {
    expect(parseRetailProductUrl(url)).toEqual({ retailer: 'amazon', productId: asin, url: `https://www.amazon.com/dp/${asin}` });
  });

  it('reads the SKU from a Best Buy URL', () => {
    expect(parseRetailProductUrl('https://www.bestbuy.com/site/apple-airpods-pro/6447382.p?skuId=6447382')).toEqual({
      retailer: 'bestbuy',
      productId: '6447382',
      url: 'https://www.bestbuy.com/site/6447382.p?skuId=6447382'
    });
  });

  it('returns null for pages that are not a product we can price-check', () => {
    expect(parseRetailProductUrl('https://www.amazon.com/s?k=ddr5+ram')).toBeNull();
    expect(parseRetailProductUrl('https://www.amazon.com/stores/GSKILL/page/ABC')).toBeNull();
    expect(parseRetailProductUrl('https://www.bestbuy.com/product/g-skill-flare-x5-8gb/J3ZYG2T5Q9')).toBeNull();
    expect(parseRetailProductUrl('https://www.newegg.com/p/N82E16820374424')).toBeNull();
    expect(parseRetailProductUrl('https://notamazon.com.evil.example/dp/B0DN43PTB9')).toBeNull();
  });
});

describe('searchRetailers', () => {
  const searchResponse = (results: unknown[], ok = true) => ({ ok, status: ok ? 200 : 500, headers: { get: () => null }, json: async () => ({ results }) });

  it('searches only Amazon and Best Buy', async () => {
    fetchMock.mockResolvedValue(searchResponse([]));

    await searchRetailers('ddr5 ram', 'abc');

    const [endpoint, init] = fetchMock.mock.calls[0];
    expect(endpoint).toBe('https://api.context.dev/v1/web/search');
    expect(init.headers.Authorization).toBe('Bearer abc');
    expect(JSON.parse(init.body)).toMatchObject({ query: 'ddr5 ram', includeDomains: ['amazon.com', 'bestbuy.com'] });
  });

  it('keeps product pages, drops everything else, and removes duplicates', async () => {
    fetchMock.mockResolvedValue(searchResponse([
      { url: 'https://www.amazon.com/clp/B0C1TKSDKR', title: 'Amazon.com: G.SKILL Flare X5 32GB', description: 'Fast DDR5' },
      { url: 'https://www.amazon.com/G-SKILL/dp/B0C1TKSDKR?th=1', title: 'G.SKILL Flare X5 32GB (duplicate)', description: '' },
      { url: 'https://www.amazon.com/s?k=flare+x5', title: 'Search page', description: '' },
      { url: 'https://www.bestbuy.com/site/g-skill/6505727.p?skuId=6505727', title: 'G.SKILL Flare X5 - Best Buy', description: '' },
    ]));

    expect(await searchRetailers('flare x5', 'abc')).toEqual([
      { retailer: 'amazon', productId: 'B0C1TKSDKR', url: 'https://www.amazon.com/dp/B0C1TKSDKR', title: 'G.SKILL Flare X5 32GB', description: 'Fast DDR5' },
      { retailer: 'bestbuy', productId: '6505727', url: 'https://www.bestbuy.com/site/6505727.p?skuId=6505727', title: 'G.SKILL Flare X5', description: '' },
    ]);
  });

  it('returns no results when the search API fails', async () => {
    fetchMock.mockResolvedValue(searchResponse([], false));

    expect(await searchRetailers('flare x5', 'abc')).toEqual([]);
  });
});
