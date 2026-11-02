/**
 * Formatting utilities for enterprise ERP financial metrics
 */

export function formatCurrency(value: number | string | null | undefined, symbol = '₹'): string {
  if (value === null || value === undefined || value === '') return '₹ 0.00';
  const num = typeof value === 'number' ? value : parseFloat(String(value).replace(/[^0-9.-]/g, ''));
  if (isNaN(num)) return String(value);

  const isNegative = num < 0;
  const absNum = Math.abs(num);

  // Format using Indian Rupee grouping if applicable, or standard locale
  const formatted = absNum.toLocaleString('en-IN', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });

  return `${isNegative ? '-' : ''}${symbol} ${formatted}`;
}

export function formatNumber(value: number | string | null | undefined): string {
  if (value === null || value === undefined || value === '') return '0';
  const num = typeof value === 'number' ? value : parseFloat(String(value));
  if (isNaN(num)) return String(value);

  return num.toLocaleString('en-IN', {
    maximumFractionDigits: 2,
  });
}

export function formatPercentage(value: number | string | null | undefined): string {
  if (value === null || value === undefined || value === '') return '0.00%';
  const num = typeof value === 'number' ? value : parseFloat(String(value).replace('%', ''));
  if (isNaN(num)) return String(value);

  return `${num.toFixed(2)}%`;
}

export function formatDate(value: string | Date | null | undefined): string {
  if (!value) return '';
  try {
    const d = typeof value === 'string' ? new Date(value) : value;
    if (isNaN(d.getTime())) return String(value);
    return d.toISOString().split('T')[0];
  } catch {
    return String(value);
  }
}
