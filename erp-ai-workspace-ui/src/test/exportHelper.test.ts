import { describe, it, expect, vi, beforeEach } from 'vitest';
import { exportToExcel, exportToCsv } from '../utils/exportHelper';
import { ColumnDef } from '../types/api';

describe('exportHelper', () => {
  const columns: ColumnDef[] = [
    { key: 'customer', label: 'Customer Name', type: 'string' },
    { key: 'revenue', label: 'Revenue', type: 'currency' },
    { key: 'invoices', label: 'Invoice Count', type: 'number' },
  ];

  const data = [
    { customer: 'Balaji Traders', revenue: 55000.0, invoices: 12 },
    { customer: 'Kisan Kendra', revenue: 38000.0, invoices: 8 },
  ];

  beforeEach(() => {
    vi.restoreAllMocks();
    if (!window.URL.createObjectURL) {
      window.URL.createObjectURL = vi.fn(() => 'blob:mock-url');
    }
    if (!window.URL.revokeObjectURL) {
      window.URL.revokeObjectURL = vi.fn();
    }
  });

  describe('exportToExcel', () => {
    it('executes exportToExcel without throwing', () => {
      expect(() => exportToExcel(data, columns, 'test-report.xlsx')).not.toThrow();
    });

    it('handles empty data gracefully without throwing', () => {
      expect(() => exportToExcel([], columns, 'empty.xlsx')).not.toThrow();
    });
  });

  describe('exportToCsv', () => {
    it('creates Blob and triggers download link click', () => {
      const clickSpy = vi.fn();
      const createElementSpy = vi.spyOn(document, 'createElement');

      createElementSpy.mockReturnValue({
        setAttribute: vi.fn(),
        click: clickSpy,
        style: {},
      } as any);

      vi.spyOn(document.body, 'appendChild').mockImplementation(() => null as any);
      vi.spyOn(document.body, 'removeChild').mockImplementation(() => null as any);

      exportToCsv(data, columns, 'test-report.csv');

      expect(createElementSpy).toHaveBeenCalledWith('a');
      expect(clickSpy).toHaveBeenCalled();
    });
  });
});
