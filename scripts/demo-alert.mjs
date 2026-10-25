// End-to-end demo against the live site: creates a real alert, runs the price check
// for it immediately (instead of waiting for the 4-hourly job), and saves the alert
// email as a local HTML file.
//
//   npm run demo:alert -- <product URL, ASIN or SKU> <target price> [email]
//   npm run demo:alert -- https://www.bestbuy.com/site/6447382.p?skuId=6447382 260
//
// A target at or above the current price triggers the alert straight away.
import { readFileSync, writeFileSync } from 'node:fs';

const SITE = process.env.DEMO_SITE || 'https://price-oracle-demo.web.app';
const [target = 'https://www.bestbuy.com/site/6447382.p?skuId=6447382', targetPrice = '260', email = 'demo@example.com'] = process.argv.slice(2);

const token = readFileSync(new URL('../functions/.env', import.meta.url), 'utf8').match(/^DEMO_TOKEN=(.+)$/m)?.[1]?.trim();
if (!token) {
  console.error('DEMO_TOKEN is missing from functions/.env');
  process.exit(1);
}

const post = async (path, body, headers = {}) => {
  const started = Date.now();
  const res = await fetch(SITE + path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...headers },
    body: JSON.stringify(body)
  });
  const text = await res.text();
  let json;
  try {
    json = JSON.parse(text);
  } catch {
    json = { error: text.slice(0, 200) };
  }
  return { status: res.status, json, seconds: ((Date.now() - started) / 1000).toFixed(1) };
};

console.log(`\n1. Creating an alert on ${SITE}`);
console.log(`   product: ${target}\n   target:  $${targetPrice}\n   email:   ${email}`);

const created = await post('/api/create-alert', {
  email,
  userId: 'demo-run',
  targetType: 'asin',
  targetValue: target,
  targetPrice: Number(targetPrice)
});

if (!created.json.success) {
  console.error(`\n   Alert was not created (HTTP ${created.status}, ${created.seconds}s): ${created.json.error}`);
  process.exit(1);
}

const alert = created.json.alert;
console.log(`\n   Saved in ${created.seconds}s as alert ${created.json.alertId}`);
console.log(`   retailer:   ${alert.retailer}`);
console.log(`   product:    ${alert.productTitle}`);
console.log(`   live price: $${alert.currentPriceAtCreation}`);
console.log(`   forecast:   ${alert.initialProjection.recommendation}, 30-day mid $${alert.initialProjection.projectedMid}`);

console.log('\n2. Running the price check for this alert now');

const checked = await post('/api/run-alert-check', { alertId: created.json.alertId }, { 'x-demo-token': token });

if (!checked.json.success) {
  console.error(`\n   Check failed (HTTP ${checked.status}, ${checked.seconds}s): ${checked.json.error}`);
  process.exit(1);
}

const result = checked.json.result;
console.log(`\n   Checked in ${checked.seconds}s`);
console.log(`   live price: ${result.currentPrice ? '$' + result.currentPrice : 'not available'}`);
console.log(`   target:     $${result.targetPrice}`);
console.log(`   triggered:  ${result.triggered ? 'YES' : 'no'}${result.note ? ' (' + result.note + ')' : ''}`);

if (result.triggered) {
  const emailNote = {
    sent: `sent to ${email}`,
    simulated: 'not sent: no RESEND_API_KEY is configured, so delivery is simulated',
    failed: 'the email provider rejected it; see the function logs'
  }[result.email];
  console.log(`   email:      ${emailNote}`);

  writeFileSync('demo-alert-email.html', '<meta charset="utf-8">' + result.emailHtml);
  console.log('\n3. The alert email was saved to demo-alert-email.html. Open it in a browser to show it.');
  console.log('   The alert is now marked "triggered" in Firestore, so it will not fire again.');
}
console.log('');
