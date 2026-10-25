import nodemailer from 'nodemailer';
import { RETAILERS, Retailer } from '../../lib/retailers';

export { RETAILERS, Retailer };

export interface AlertEmailPayload {
  to: string;
  productTitle: string;
  retailer: Retailer;
  productId: string; // Amazon ASIN or Best Buy SKU
  currentPrice: number;
  originalPrice?: number;
  projectedMid: number;
  lowestPrice: number;
  recommendation: string;
  deltaPercentMonth: number;
}

export type EmailStatus = 'sent' | 'simulated' | 'failed';

export function renderAlertEmail(payload: AlertEmailPayload) {
  const retailer = RETAILERS[payload.retailer];
  const productUrl = retailer.productUrl(payload.productId);
  const htmlBody = `
    <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; max-width: 600px; margin: 0 auto; padding: 24px; border: 1px solid #e2e8f0; border-radius: 16px;">
      <div style="background: #10b981; padding: 12px 18px; border-radius: 12px; color: white; font-weight: bold; font-size: 14px; display: inline-block;">
        ⚡ PriceOracle Sentinel Alert
      </div>
      
      <h2 style="color: #0f172a; margin-top: 18px; font-size: 20px;">Price Drop Alert for: ${payload.productTitle}</h2>
      
      <div style="margin: 20px 0; background: #f8fafc; padding: 18px; border-radius: 12px;">
        <p style="font-size: 14px; color: #64748b; margin: 0;">Current ${retailer.name} Price</p>
        <p style="font-size: 32px; font-weight: 800; color: #0f172a; margin: 4px 0 0 0;">$${payload.currentPrice.toFixed(2)}</p>
        <p style="font-size: 13px; color: #16a34a; font-weight: bold; margin-top: 6px;">
          Oracle Recommendation: ${payload.recommendation} (Projected Floor: $${payload.lowestPrice.toFixed(2)})
        </p>
      </div>

      <p style="font-size: 14px; color: #475569; line-height: 1.5;">
        This item has entered your target discount threshold or is predicted to move. 1-Month projected change is currently <strong>${payload.deltaPercentMonth}%</strong>.
      </p>

      <div style="margin-top: 24px;">
        <a href="${productUrl}" style="background: #0f172a; color: white; text-decoration: none; padding: 12px 24px; border-radius: 10px; font-weight: bold; font-size: 14px; display: inline-block;">
          View Deal on ${retailer.name} &rarr;
        </a>
      </div>

      <hr style="margin: 32px 0 16px 0; border: none; border-top: 1px solid #e2e8f0;" />
      <p style="font-size: 11px; color: #94a3b8;">
        Sent automatically by PriceOracle Alertant • Verified by context.dev & prediction markets.
      </p>
    </div>
  `;

  return {
    subject: `🔥 Price Alert: ${payload.productTitle} hit $${payload.currentPrice.toFixed(2)}`,
    html: htmlBody
  };
}

async function sendEmail(to: string, subject: string, html: string, simulationNote: string): Promise<EmailStatus> {
  // A Gmail account can send to any address; Resend can only do that from a verified domain
  const gmailUser = process.env.GMAIL_USER;
  const gmailAppPassword = process.env.GMAIL_APP_PASSWORD;

  if (gmailUser && gmailAppPassword) {
    try {
      const transport = nodemailer.createTransport({
        host: 'smtp.gmail.com',
        port: 465,
        secure: true,
        auth: { user: gmailUser, pass: gmailAppPassword.replace(/\s/g, '') }
      });
      await transport.sendMail({ from: `PriceOracle <${gmailUser}>`, to, subject, html });
      console.log(`[Email] Sent via Gmail to ${to}: ${subject}`);
      return 'sent';
    } catch (e) {
      console.error('[Email] Failed to send via Gmail:', e);
      return 'failed';
    }
  }

  const apiKey = process.env.RESEND_API_KEY;

  if (apiKey && apiKey.startsWith('re_') && apiKey !== 're_demo_key') {
    try {
      const res = await fetch('https://api.resend.com/emails', {
        method: 'POST',
        headers: {
          'Authorization': `Bearer ${apiKey}`,
          'Content-Type': 'application/json'
        },
        body: JSON.stringify({
          from: process.env.ALERT_FROM_EMAIL || 'PriceOracle <alerts@priceoracle.app>',
          to,
          subject,
          html
        })
      });
      if (!res.ok) {
        console.error(`[Email] Resend rejected the email (${res.status}):`, await res.text());
        return 'failed';
      }
      console.log(`[Email] Sent to ${to}: ${subject}`);
      return 'sent';
    } catch (e) {
      console.error('[Email] Failed to send via Resend:', e);
      return 'failed';
    }
  } else {
    console.log(`[Email-Simulation] Dispatched simulated email to ${to} for ${simulationNote}`);
    return 'simulated';
  }
}

export async function sendPriceAlertEmail(payload: AlertEmailPayload): Promise<EmailStatus> {
  const { subject, html } = renderAlertEmail(payload);
  return sendEmail(payload.to, subject, html, `${payload.retailer}:${payload.productId}`);
}

// Category and keyword alerts are saved but not price-checked yet, so tell the subscriber where things stand
export async function sendCategoryAlertNotice(to: string, category: string): Promise<EmailStatus> {
  const safeCategory = category.replace(/[<>&]/g, '');
  const html = `
    <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; max-width: 600px; margin: 0 auto; padding: 24px; border: 1px solid #e2e8f0; border-radius: 16px;">
      <div style="background: #10b981; padding: 12px 18px; border-radius: 12px; color: white; font-weight: bold; font-size: 14px; display: inline-block;">
        PriceOracle Sentinel
      </div>

      <h2 style="color: #0f172a; margin-top: 18px; font-size: 20px;">Your ${safeCategory} alert is saved. Category tracking is a work in progress.</h2>

      <p style="font-size: 14px; color: #475569; line-height: 1.5;">
        We have recorded your alert for <strong>${safeCategory}</strong>, but category-wide price tracking is still being built, so this alert will not send price drops yet.
      </p>

      <p style="font-size: 14px; color: #475569; line-height: 1.5;">
        Alerts on a specific Amazon or Best Buy product work today: paste the product link on PriceOracle and we will email you when it reaches your target price.
      </p>

      <hr style="margin: 32px 0 16px 0; border: none; border-top: 1px solid #e2e8f0;" />
      <p style="font-size: 11px; color: #94a3b8;">
        Sent automatically by PriceOracle Alertant.
      </p>
    </div>
  `;

  return sendEmail(to, `Your ${safeCategory} alert is saved (category tracking is in progress)`, html, `category:${safeCategory}`);
}

export interface AlertConfirmationPayload {
  to: string;
  productTitle: string;
  retailer: Retailer;
  productId: string;
  currentPrice: number;
  targetPrice: number | null;
  minDiscountPercent: number | null;
  recommendation?: string;
  rangeLow?: number;
  rangeHigh?: number;
}

export function renderAlertConfirmationEmail(payload: AlertConfirmationPayload) {
  const retailer = RETAILERS[payload.retailer];
  const productUrl = retailer.productUrl(payload.productId);
  const safeTitle = payload.productTitle.replace(/[<>&]/g, '');

  // The same rule the price check applies (lib/alert-rules.ts), in words
  const trigger = payload.targetPrice
    ? `drops to <strong>$${payload.targetPrice.toFixed(2)}</strong> or lower`
    : payload.minDiscountPercent
    ? `is discounted by <strong>${payload.minDiscountPercent}%</strong> or more`
    : `drops to <strong>$${(payload.currentPrice * 0.95).toFixed(2)}</strong> or lower (5% below today's price)`;

  const forecast = payload.recommendation && payload.rangeLow && payload.rangeHigh
    ? `<p style="font-size: 13px; color: #475569; margin: 8px 0 0 0;">Our 30-day outlook: <strong>${payload.recommendation}</strong>, typical range $${payload.rangeLow.toFixed(2)} to $${payload.rangeHigh.toFixed(2)}.</p>`
    : '';

  const html = `
    <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; max-width: 600px; margin: 0 auto; padding: 24px; border: 1px solid #e2e8f0; border-radius: 16px;">
      <div style="background: #10b981; padding: 12px 18px; border-radius: 12px; color: white; font-weight: bold; font-size: 14px; display: inline-block;">
        PriceOracle Sentinel: alert active
      </div>

      <h2 style="color: #0f172a; margin-top: 18px; font-size: 20px;">We are watching ${safeTitle}</h2>

      <div style="margin: 20px 0; background: #f8fafc; padding: 18px; border-radius: 12px;">
        <p style="font-size: 14px; color: #64748b; margin: 0;">${retailer.name} price right now</p>
        <p style="font-size: 32px; font-weight: 800; color: #0f172a; margin: 4px 0 0 0;">$${payload.currentPrice.toFixed(2)}</p>
        ${forecast}
      </div>

      <p style="font-size: 14px; color: #475569; line-height: 1.5;">
        We check this price every few hours and will email you as soon as it ${trigger}.
      </p>

      <div style="margin-top: 24px;">
        <a href="${productUrl}" style="background: #0f172a; color: white; text-decoration: none; padding: 12px 24px; border-radius: 10px; font-weight: bold; font-size: 14px; display: inline-block;">
          View on ${retailer.name} &rarr;
        </a>
      </div>

      <hr style="margin: 32px 0 16px 0; border: none; border-top: 1px solid #e2e8f0;" />
      <p style="font-size: 11px; color: #94a3b8;">
        You are receiving this because a price alert was set for this address on PriceOracle Alertant.
      </p>
    </div>
  `;

  return { subject: `Alert set: ${safeTitle.slice(0, 70)} at $${payload.currentPrice.toFixed(2)}`, html };
}

// Sent once, when a product alert is created
export async function sendAlertConfirmationEmail(payload: AlertConfirmationPayload): Promise<EmailStatus> {
  const { subject, html } = renderAlertConfirmationEmail(payload);
  return sendEmail(payload.to, subject, html, `confirmation ${payload.retailer}:${payload.productId}`);
}
