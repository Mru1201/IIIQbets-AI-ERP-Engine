import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, within, waitFor } from '@testing-library/react';
import * as XLSX from 'xlsx';
import fs from 'fs';
import path from 'path';
import { HighDensityTable } from '../components/Zone2_CenterCanvas/HighDensityTable';
import { KPIHeader } from '../components/Zone1_TopHeader/KPIHeader';
import { ChartCanvas } from '../components/Zone2_CenterCanvas/ChartCanvas';
import { JouleDrawer } from '../components/Zone3_JouleCopilot/JouleDrawer';
import { FormulaInspector } from '../components/Zone3_JouleCopilot/FormulaInspector';
import { AnalyticalListPage } from '../components/AnalyticalListPage';
import { exportToExcel, exportToCsv } from '../utils/exportHelper';
import { ColumnDef, KPICard } from '../types/api';

// Global ResizeObserver mock for Recharts in jsdom
global.ResizeObserver = class {
  observe() {}
  unobserve() {}
  disconnect() {}
};

describe('Milestone 4 Adversarial Stress Testing Suite', () => {
  const generatedFiles: string[] = [];

  beforeEach(() => {
    vi.restoreAllMocks();
    if (!window.URL.createObjectURL) {
      window.URL.createObjectURL = vi.fn(() => 'blob:mock-url');
    }
    if (!window.URL.revokeObjectURL) {
      window.URL.revokeObjectURL = vi.fn();
    }
    // Prevent jsdom navigation error on link.click()
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
  });

  afterEach(() => {
    // Clean up any files generated on disk
    generatedFiles.forEach((file) => {
      try {
        if (fs.existsSync(file)) fs.unlinkSync(file);
      } catch {
        // ignore
      }
    });
    generatedFiles.length = 0;
  });

  // =========================================================================
  // SECTION 1: Empty, Boundary, and Extreme Dataset Handling
  // =========================================================================
  describe('1. Empty, Boundary, and Extreme Dataset Handling', () => {
    const defaultCols: ColumnDef[] = [
      { key: 'customer', label: 'Customer Name', type: 'string', sortable: true },
      { key: 'city', label: 'City', type: 'string', sortable: true },
      { key: 'balance', label: 'Unpaid Balance', type: 'currency', sortable: true },
      { key: 'invoices', label: 'Invoices', type: 'number', sortable: true },
      { key: 'last_date', label: 'Last Date', type: 'date', sortable: true },
    ];

    it('renders HighDensityTable with 0 rows without throwing, displays empty message and pagination indicator', () => {
      render(<HighDensityTable data={[]} columns={defaultCols} />);

      expect(screen.getByText('No matching records found.')).toBeInTheDocument();
      expect(screen.getByText(/0 of 0 records/i)).toBeInTheDocument();

      // Check pagination indicator
      const paginationSpan = screen.getByText((_, element) => {
        return (
          element?.tagName.toLowerCase() === 'span' &&
          element.textContent?.trim() === 'Page 1 of 1'
        );
      });
      expect(paginationSpan).toBeInTheDocument();

      // Pagination buttons should be disabled
      const prevBtn = screen.getByTitle('Previous Page');
      const nextBtn = screen.getByTitle('Next Page');
      expect(prevBtn).toBeDisabled();
      expect(nextBtn).toBeDisabled();
    });

    it('renders HighDensityTable with 0 columns and 0 rows without crashing', () => {
      expect(() => {
        render(<HighDensityTable data={[]} columns={[]} />);
      }).not.toThrow();
    });

    it('safely handles missing, null, and undefined values in rows across currency, number, date, and string without TypeError', () => {
      const corruptData = [
        { customer: null, city: undefined, balance: null, invoices: undefined, last_date: null },
        { customer: 'Test Co', balance: undefined, invoices: null, last_date: 'invalid-date' },
        { /* completely missing keys */ },
      ];

      render(<HighDensityTable data={corruptData} columns={defaultCols} />);

      // All missing values should render the placeholder '-'
      const dashes = screen.getAllByText('-');
      expect(dashes.length).toBeGreaterThan(0);
      expect(screen.getByText('Test Co')).toBeInTheDocument();
    });

    it('safely handles extreme numeric values (NaN, Infinity, huge numbers, negative decimals) without crashing', () => {
      const extremeData = [
        { customer: 'Infinity Corp', city: 'Cloud', balance: Infinity, invoices: -Infinity, last_date: null },
        { customer: 'NaN LLC', city: 'Void', balance: NaN, invoices: NaN, last_date: null },
        { customer: 'Huge Enterprise', city: 'Metropolis', balance: 999999999999999.99, invoices: 10000000, last_date: null },
        { customer: 'Subzero Agro', city: 'Tundra', balance: -43232.55, invoices: -15, last_date: null },
      ];

      expect(() => {
        render(<HighDensityTable data={extremeData} columns={defaultCols} />);
      }).not.toThrow();

      // Negative currency should be formatted with minus sign and INR symbol
      expect(screen.getByText('-₹ 43,232.55')).toBeInTheDocument();
      expect(screen.getByText('-15')).toBeInTheDocument();
    });

    it('handles extreme strings, HTML/XSS injection attempts, and Unicode in HighDensityTable safely', () => {
      const xssData = [
        {
          customer: '<script>alert("xss")</script>',
          city: '<img src=x onerror=alert(1)>',
          balance: 1000,
          invoices: 1,
          last_date: '2026-01-01',
        },
        {
          customer: 'Balaji Agro 🌾🚜',
          city: 'मराठी / हिन्दी शहर',
          balance: 2000,
          invoices: 2,
          last_date: '2026-01-02',
        },
        {
          customer: 'A'.repeat(500),
          city: 'B'.repeat(200),
          balance: 3000,
          invoices: 3,
          last_date: '2026-01-03',
        },
      ];

      render(<HighDensityTable data={xssData} columns={defaultCols} />);

      // Script text should be rendered as escaped text content
      expect(screen.getByText('<script>alert("xss")</script>')).toBeInTheDocument();
      expect(screen.getByText('Balaji Agro 🌾🚜')).toBeInTheDocument();
      expect(screen.getByText('मराठी / हिन्दी शहर')).toBeInTheDocument();
    });

    it('renders KPIHeader with empty, null, or undefined array gracefully', () => {
      const { container: c1 } = render(<KPIHeader kpis={[]} />);
      expect(c1.firstChild).toBeNull();

      const { container: c2 } = render(<KPIHeader kpis={null as any} />);
      expect(c2.firstChild).toBeNull();

      const { container: c3 } = render(<KPIHeader kpis={undefined as any} />);
      expect(c3.firstChild).toBeNull();
    });

    it('renders KPI cards with null and undefined values without throwing TypeError', () => {
      const corruptKpis: KPICard[] = [
        { label: 'Null Value KPI', value: null as any, trend: null },
        { label: 'Undefined Value KPI', value: undefined as any, trend: undefined },
        { label: 'Empty String Value', value: '', trend: 'neutral' },
      ];

      expect(() => {
        render(<KPIHeader kpis={corruptKpis} />);
      }).not.toThrow();

      expect(screen.getByText('Null Value KPI')).toBeInTheDocument();
      expect(screen.getByText('Undefined Value KPI')).toBeInTheDocument();
      expect(screen.getByText('Empty String Value')).toBeInTheDocument();
    });

    it('renders negative numbers and negative trends properly in KPIHeader with Down trend badge', () => {
      const kpis: KPICard[] = [
        {
          label: 'Net Loss',
          value: -43232.0,
          trend: 'negative',
          subtext: 'Operating Loss',
          metric_key: 'net_profit',
        },
        {
          label: 'String Net Loss',
          value: '-43,232.00 INR',
          trend: 'negative',
          subtext: 'Calculated Net Loss',
          metric_key: 'net_profit',
        },
      ];

      render(<KPIHeader kpis={kpis} />);

      // Trend indicators
      const downBadges = screen.getAllByText('Down');
      expect(downBadges.length).toBe(2);

      // Formatted value checks
      expect(screen.getByText('-43,232')).toBeInTheDocument();
      expect(screen.getByText('-₹ 43,232.00')).toBeInTheDocument();
    });

    it('renders extreme magnitudes in KPIHeader without throwing', () => {
      const extremeKpis: KPICard[] = [
        { label: 'Max Safe', value: Number.MAX_SAFE_INTEGER, trend: 'positive' },
        { label: 'Min Safe', value: Number.MIN_SAFE_INTEGER, trend: 'negative' },
        { label: 'Zero', value: 0, trend: 'neutral' },
      ];

      expect(() => {
        render(<KPIHeader kpis={extremeKpis} />);
      }).not.toThrow();

      expect(screen.getByText('Zero')).toBeInTheDocument();
      expect(screen.getByText('0')).toBeInTheDocument();
    });

    it('handles ChartCanvas with 0 rows gracefully by returning null', () => {
      const { container } = render(
        <ChartCanvas
          data={[]}
          columns={defaultCols}
          suggestedVisualization="bar_chart"
        />
      );
      expect(container.firstChild).toBeNull();
    });

    it('handles ChartCanvas with no numeric columns gracefully by returning null', () => {
      const stringOnlyCols: ColumnDef[] = [
        { key: 'name', label: 'Name', type: 'string' },
        { key: 'city', label: 'City', type: 'string' },
      ];
      const { container } = render(
        <ChartCanvas
          data={[{ name: 'A', city: 'B' }]}
          columns={stringOnlyCols}
          suggestedVisualization="bar_chart"
        />
      );
      expect(container.firstChild).toBeNull();
    });

    it('renders ChartCanvas with negative values without throwing', () => {
      const negData = [
        { customer: 'Loss Co A', balance: -15000.0 },
        { customer: 'Loss Co B', balance: -25000.0 },
      ];

      expect(() => {
        render(
          <ChartCanvas
            data={negData}
            columns={[
              { key: 'customer', label: 'Customer', type: 'string' },
              { key: 'balance', label: 'Balance', type: 'currency' },
            ]}
            suggestedVisualization="bar_chart"
          />
        );
      }).not.toThrow();
    });

    it('renders JouleDrawer when metadata is undefined without throwing', () => {
      render(
        <JouleDrawer
          isOpen={true}
          onToggle={() => {}}
          metadata={undefined}
          rowCount={0}
          onSubmitQuery={() => {}}
        />
      );

      expect(
        screen.getByText(/Execute an AI report query to view step-by-step reasoning/i)
      ).toBeInTheDocument();
    });

    it('handles FormulaInspector with unknown metricKey using fallback definition without throwing', () => {
      render(<FormulaInspector metricKey="non_existent_unknown_metric" onClose={() => {}} />);

      expect(screen.getByText(/Formula Inspector: NON EXISTENT UNKNOWN METRIC/i)).toBeInTheDocument();
      expect(screen.getByText(/Key: non_existent_unknown_metric/i)).toBeInTheDocument();
      expect(screen.getByText('SELECT non_existent_unknown_metric FROM ...')).toBeInTheDocument();
    });

    it('renders full AnalyticalListPage when backend returns 0 rows and 0 KPIs without throwing', async () => {
      global.fetch = vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({
          status: 'success',
          execution_metadata: {
            thought_process: 'Query returned zero records for given criteria.',
            tables_used: ['voucher'],
            metrics_applied: [],
            verified_sql: 'SELECT * FROM voucher WHERE 1=0',
            retries_used: 0,
            retry_history: [],
            latency_ms: 120,
          },
          ui_schema: {
            suggested_visualization: 'table',
            columns: [
              { key: 'customer', label: 'Customer', type: 'string' },
              { key: 'amount', label: 'Amount', type: 'currency' },
            ],
            kpis: [],
            filters: [],
          },
          data: [],
          error: null,
        }),
      } as any);

      render(<AnalyticalListPage defaultQuery="Empty query test" />);

      await waitFor(() => {
        expect(screen.getByText('No matching records found.')).toBeInTheDocument();
      });
      expect(screen.getByText(/0 of 0 records/i)).toBeInTheDocument();
    });
  });

  // =========================================================================
  // SECTION 2: Multi-Column Sorting and Column Visibility State Integrity
  // =========================================================================
  describe('2. Multi-Column Sorting and Column Visibility Toggle State Integrity', () => {
    const testData = [
      { dept: 'Sales', name: 'Charlie', score: 90, salary: 50000 },
      { dept: 'Engineering', name: 'Alice', score: 85, salary: 80000 },
      { dept: 'Sales', name: 'Bob', score: 95, salary: 60000 },
      { dept: 'Engineering', name: 'David', score: 70, salary: 75000 },
      { dept: 'Sales', name: 'Alice', score: 80, salary: 55000 },
    ];

    const sortCols: ColumnDef[] = [
      { key: 'dept', label: 'Department', type: 'string', sortable: true },
      { key: 'name', label: 'Employee Name', type: 'string', sortable: true },
      { key: 'score', label: 'Performance Score', type: 'number', sortable: true },
      { key: 'salary', label: 'Salary', type: 'currency', sortable: true },
    ];

    it('toggles single column sorting asc/desc/none on header click', () => {
      render(<HighDensityTable data={testData} columns={sortCols} />);

      const deptHeader = screen.getByText('Department');

      // 1st click -> asc
      fireEvent.click(deptHeader);
      let rows = screen.getAllByRole('row');
      expect(within(rows[1]).getByText('Engineering')).toBeInTheDocument();
      expect(within(rows[2]).getByText('Engineering')).toBeInTheDocument();

      // 2nd click -> desc
      fireEvent.click(deptHeader);
      rows = screen.getAllByRole('row');
      expect(within(rows[1]).getByText('Sales')).toBeInTheDocument();
    });

    it('supports Shift+Click multi-column sorting (dept asc, then name asc tie-break)', () => {
      render(<HighDensityTable data={testData} columns={sortCols} />);

      const deptHeader = screen.getByText('Department');
      const nameHeader = screen.getByText('Employee Name');

      // Click Dept (asc)
      fireEvent.click(deptHeader);

      // Shift+Click Name (asc) to add second sorting dimension
      fireEvent.click(nameHeader, { shiftKey: true });

      const rows = screen.getAllByRole('row');
      // Engineering Alice, then Engineering David
      expect(within(rows[1]).getByText('Engineering')).toBeInTheDocument();
      expect(within(rows[1]).getByText('Alice')).toBeInTheDocument();

      expect(within(rows[2]).getByText('Engineering')).toBeInTheDocument();
      expect(within(rows[2]).getByText('David')).toBeInTheDocument();

      // Sales Alice, then Sales Bob, then Sales Charlie
      expect(within(rows[3]).getByText('Sales')).toBeInTheDocument();
      expect(within(rows[3]).getByText('Alice')).toBeInTheDocument();

      expect(within(rows[4]).getByText('Sales')).toBeInTheDocument();
      expect(within(rows[4]).getByText('Bob')).toBeInTheDocument();

      expect(within(rows[5]).getByText('Sales')).toBeInTheDocument();
      expect(within(rows[5]).getByText('Charlie')).toBeInTheDocument();
    });

    it('toggles multiple column visibilities off and on and verifies DOM table columns match', () => {
      render(<HighDensityTable data={testData} columns={sortCols} />);

      const toggleBtn = screen.getByTitle('Toggle visible columns');
      fireEvent.click(toggleBtn);

      // Verify all 4 checkboxes exist and are checked
      const deptCb = screen.getByLabelText('Department');
      const nameCb = screen.getByLabelText('Employee Name');
      const scoreCb = screen.getByLabelText('Performance Score');
      const salaryCb = screen.getByLabelText('Salary');

      expect(deptCb).toBeChecked();
      expect(nameCb).toBeChecked();
      expect(scoreCb).toBeChecked();
      expect(salaryCb).toBeChecked();

      const tableElement = screen.getByRole('table');

      // Hide Salary column
      fireEvent.click(salaryCb);
      expect(salaryCb).not.toBeChecked();
      expect(within(tableElement).queryByText('Salary')).not.toBeInTheDocument();
      expect(within(tableElement).queryByText('₹ 80,000.00')).not.toBeInTheDocument();

      // Hide Score column
      fireEvent.click(scoreCb);
      expect(scoreCb).not.toBeChecked();
      expect(within(tableElement).queryByText('Performance Score')).not.toBeInTheDocument();

      // Department and Name should still be visible in table
      expect(within(tableElement).getByText('Department')).toBeInTheDocument();
      expect(within(tableElement).getByText('Employee Name')).toBeInTheDocument();

      // Re-enable Salary
      fireEvent.click(salaryCb);
      expect(salaryCb).toBeChecked();
      expect(within(tableElement).getByText('Salary')).toBeInTheDocument();
      expect(within(tableElement).getByText('₹ 80,000.00')).toBeInTheDocument();
    });

    it('handles toggling all columns off without throwing runtime exceptions', () => {
      render(<HighDensityTable data={testData} columns={sortCols} />);

      const toggleBtn = screen.getByTitle('Toggle visible columns');
      fireEvent.click(toggleBtn);

      ['Department', 'Employee Name', 'Performance Score', 'Salary'].forEach((col) => {
        const cb = screen.getByLabelText(col);
        fireEvent.click(cb);
      });

      // Table should render without exploding, 0 table headers
      const table = screen.getByRole('table');
      expect(table).toBeInTheDocument();
    });

    it('preserves sorting order when column visibility is toggled', () => {
      render(<HighDensityTable data={testData} columns={sortCols} />);

      // Sort by Name asc
      const nameHeader = screen.getByText('Employee Name');
      fireEvent.click(nameHeader);

      let rows = screen.getAllByRole('row');
      expect(within(rows[1]).getAllByText('Alice').length).toBeGreaterThan(0);

      // Hide Department column
      const toggleBtn = screen.getByTitle('Toggle visible columns');
      fireEvent.click(toggleBtn);
      const deptCb = screen.getByLabelText('Department');
      fireEvent.click(deptCb);

      // Name sort should still be preserved
      rows = screen.getAllByRole('row');
      expect(within(rows[1]).getAllByText('Alice').length).toBeGreaterThan(0);
    });
  });

  // =========================================================================
  // SECTION 3: SheetJS XLSX and CSV Export Under Adversarial Inputs
  // =========================================================================
  describe('3. SheetJS XLSX and CSV Export Under Adversarial Inputs', () => {
    const exportCols: ColumnDef[] = [
      { key: 'name', label: 'Company Name', type: 'string' },
      { key: 'remarks', label: 'Notes & Remarks', type: 'string' },
      { key: 'amount', label: 'Amount', type: 'currency' },
      { key: 'items', label: 'Total Items', type: 'number' },
    ];

    it('exports CSV with special characters (quotes, commas, newlines, semicolons, unicode) following RFC 4180', () => {
      let exportedCsvContent = '';

      const originalBlob = window.Blob;
      window.Blob = class MockBlob extends originalBlob {
        constructor(parts?: BlobPart[], options?: BlobPropertyBag) {
          super(parts, options);
          if (parts && parts.length > 0) {
            exportedCsvContent = parts.map((p) => String(p)).join('');
          }
        }
      };

      const trickyData = [
        {
          name: 'Balaji & Sons, "Agro" Division',
          remarks: 'Line 1\nLine 2\r\nLine 3; with; semicolons and\ttabs',
          amount: 55000.5,
          items: 10,
        },
        {
          name: 'Hindi/Arabic: श्री शाश्वत राज / مرحباً',
          remarks: 'Emoji notes: 🌾🚜🚀 "quoted text"',
          amount: -12345.67,
          items: 0,
        },
      ];

      exportToCsv(trickyData, exportCols, 'adversarial_export.csv');

      // Verify UTF-8 BOM
      expect(exportedCsvContent.startsWith('\uFEFF')).toBe(true);

      const content = exportedCsvContent.slice(1);
      const lines = content.split('\r\n');

      // Header row
      expect(lines[0]).toBe('"Company Name","Notes & Remarks","Amount","Total Items"');

      // Verify double-quote escaping inside fields
      expect(content).toContain('Balaji & Sons, ""Agro"" Division');
      expect(content).toContain('""quoted text""');

      // Verify multi-line string preserved inside quotes
      expect(content).toContain('Line 1\nLine 2\r\nLine 3');

      // Verify Unicode preserved
      expect(content).toContain('श्री शाश्वत राज / مرحباً');
      expect(content).toContain('🌾🚜🚀');

      window.Blob = originalBlob;
    });

    it('exports CSV safely when data contains null, undefined, empty strings, and missing keys', () => {
      let captured = '';
      const originalBlob = window.Blob;
      window.Blob = class MockBlob extends originalBlob {
        constructor(parts?: BlobPart[], options?: BlobPropertyBag) {
          super(parts, options);
          if (parts) captured = parts.join('');
        }
      };

      const nullData = [
        { name: null, remarks: undefined, amount: '', items: null },
        { /* completely missing keys */ },
      ];

      expect(() => {
        exportToCsv(nullData, exportCols, 'null_export.csv');
      }).not.toThrow();

      expect(captured).toContain('""');

      window.Blob = originalBlob;
    });

    it('exports CSV for large dataset (5,000 rows) with high throughput and zero corruption', () => {
      let totalLines = 0;
      const originalBlob = window.Blob;
      window.Blob = class MockBlob extends originalBlob {
        constructor(parts?: BlobPart[], options?: BlobPropertyBag) {
          super(parts, options);
          if (parts) {
            const raw = parts.join('');
            totalLines = raw.split('\r\n').length;
          }
        }
      };

      const largeData = Array.from({ length: 5000 }, (_, i) => ({
        name: `Dealer #${i}`,
        remarks: `Auto-generated benchmark test line #${i}`,
        amount: (i * 123.45) % 100000,
        items: i % 50,
      }));

      const t0 = performance.now();
      exportToCsv(largeData, exportCols, 'large_export.csv');
      const elapsed = performance.now() - t0;

      // 5000 data rows + 1 header row = 5001 lines
      expect(totalLines).toBe(5001);
      expect(elapsed).toBeLessThan(1000);

      window.Blob = originalBlob;
    });

    it('exports SheetJS XLSX with special characters and verifies physical workbook file on disk', () => {
      const complexData = [
        {
          name: 'Agro Corp "Global"',
          remarks: 'Special, comma & "quotes" & € symbol',
          amount: 88500.0,
          items: 42,
        },
        {
          name: 'Empty Notes Inc',
          remarks: null,
          amount: -999.99,
          items: 0,
        },
      ];

      const testFile = 'adversarial_audit_test.xlsx';
      generatedFiles.push(testFile);

      exportToExcel(complexData, exportCols, testFile);

      // Verify physical file was written by SheetJS
      expect(fs.existsSync(testFile)).toBe(true);

      // Read back workbook using SheetJS with cellStyles: true to inspect !cols
      const workbook = XLSX.readFile(testFile, { cellStyles: true });
      expect(workbook.SheetNames).toContain('AI Report');

      const worksheet = workbook.Sheets['AI Report'];
      const parsedData: any[] = XLSX.utils.sheet_to_json(worksheet);

      expect(parsedData.length).toBe(2);
      expect(parsedData[0]['Company Name']).toBe('Agro Corp "Global"');
      expect(parsedData[0]['Notes & Remarks']).toBe('Special, comma & "quotes" & € symbol');
      expect(parsedData[0]['Amount']).toBe(88500);
      expect(parsedData[0]['Total Items']).toBe(42);

      expect(parsedData[1]['Company Name']).toBe('Empty Notes Inc');
      expect(parsedData[1]['Amount']).toBe(-999.99);

      // Column widths check
      expect(worksheet['!cols']).toBeDefined();
      expect(worksheet['!cols']!.length).toBe(4);
    });

    it('exports SheetJS XLSX for large dataset (5,000 rows) efficiently without memory exhaustion', () => {
      const largeData = Array.from({ length: 5000 }, (_, i) => ({
        name: `Customer ${i}`,
        remarks: `Note for transaction ${i}`,
        amount: i * 10,
        items: i,
      }));

      const testFile = 'large_adversarial_test.xlsx';
      generatedFiles.push(testFile);

      const t0 = performance.now();
      exportToExcel(largeData, exportCols, testFile);
      const elapsed = performance.now() - t0;

      expect(fs.existsSync(testFile)).toBe(true);

      // Read back and verify row count
      const workbook = XLSX.readFile(testFile);
      const worksheet = workbook.Sheets['AI Report'];
      const parsed = XLSX.utils.sheet_to_json(worksheet);

      expect(parsed.length).toBe(5000);
      expect(elapsed).toBeLessThan(2000);
    });

    it('handles empty datasets (0 rows) gracefully in both exportToExcel and exportToCsv without creating files', () => {
      const testXlsx = 'empty_test_should_not_exist.xlsx';
      generatedFiles.push(testXlsx);

      const createObjectURLSpy = vi.spyOn(window.URL, 'createObjectURL');

      exportToExcel([], exportCols, testXlsx);
      exportToCsv([], exportCols, 'empty_test.csv');

      // Empty dataset should return early without creating file or object URL
      expect(fs.existsSync(testXlsx)).toBe(false);
      expect(createObjectURLSpy).not.toHaveBeenCalled();
    });
  });
});
