// SPDX-License-Identifier: AGPL-3.0-only
const USD_RATES = { usd: 1, eur: 0.92, gbp: 0.79, pkr: 278, aed: 3.6725 };
const CURRENCY_CODES = { usd: "USD", eur: "EUR", gbp: "GBP", pkr: "PKR", aed: "AED" };

export function normalizeCurrency(value) {
  const id = String(value || "usd").toLowerCase();
  return CURRENCY_CODES[id] ? id : "usd";
}

export function convertFromUsd(value, currency = "usd") {
  const amount = Number(value || 0);
  return amount * (USD_RATES[normalizeCurrency(currency)] || 1);
}

export function formatCurrency(value, currency = "usd", fractionDigits = 0) {
  const id = normalizeCurrency(currency);
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: CURRENCY_CODES[id],
    maximumFractionDigits: fractionDigits,
  }).format(convertFromUsd(value, id));
}
