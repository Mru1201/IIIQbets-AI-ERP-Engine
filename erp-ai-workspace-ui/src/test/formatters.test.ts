import { describe, it, expect } from 'vitest';
import {
  formatCurrency,
  formatNumber,
  formatPercentage,
  formatDate,
} from '../utils/formatters';

describe('formatters', () => {
  describe('formatCurrency', () => {
    it('formats positive currency with Indian Rupee symbol', () => {
      const res = formatCurrency(123456.78);
      expect(res).toBe('₹ 1,23,456.78');
    });

    it('formats negative currency with minus sign', () => {
      const res = formatCurrency(-43232.0);
      expect(res).toBe('-₹ 43,232.00');
    });

    it('handles string input cleanly', () => {
      const res = formatCurrency('10368.00 INR');
      expect(res).toBe('₹ 10,368.00');
    });

    it('handles null, undefined and empty values', () => {
      expect(formatCurrency(null)).toBe('₹ 0.00');
      expect(formatCurrency(undefined)).toBe('₹ 0.00');
      expect(formatCurrency('')).toBe('₹ 0.00');
    });
  });

  describe('formatNumber', () => {
    it('formats numbers with commas', () => {
      expect(formatNumber(1000)).toBe('1,000');
      expect(formatNumber(1000000)).toBe('10,00,000');
    });

    it('handles zero and nulls', () => {
      expect(formatNumber(0)).toBe('0');
      expect(formatNumber(null)).toBe('0');
    });
  });

  describe('formatPercentage', () => {
    it('formats decimal percentages', () => {
      expect(formatPercentage(24.37)).toBe('24.37%');
      expect(formatPercentage('18.5%')).toBe('18.50%');
    });
  });

  describe('formatDate', () => {
    it('formats date strings to YYYY-MM-DD', () => {
      expect(formatDate('2026-04-17T18:30:00Z')).toBe('2026-04-17');
      expect(formatDate(new Date('2026-11-02T00:00:00Z'))).toBe('2026-11-02');
    });
  });
});
