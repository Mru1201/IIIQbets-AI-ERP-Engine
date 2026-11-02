import * as XLSX from 'xlsx';
import { ColumnDef } from '../types/api';

/**
 * 1-Click Client-Side Export Engine for ERP Reports
 */

export function exportToExcel(
  data: Record<string, any>[],
  columns: ColumnDef[],
  filename = 'erp-ai-report.xlsx'
): void {
  if (!data || data.length === 0) return;

  // Map data to display labels
  const formattedData = data.map((row) => {
    const newRow: Record<string, any> = {};
    columns.forEach((col) => {
      newRow[col.label || col.key] = row[col.key] !== undefined ? row[col.key] : '';
    });
    return newRow;
  });

  const worksheet = XLSX.utils.json_to_sheet(formattedData);

  // Auto-fit column widths
  const colWidths = columns.map((col) => {
    const headerLen = (col.label || col.key).length;
    const maxValLen = data.reduce((max, r) => {
      const v = String(r[col.key] ?? '');
      return Math.max(max, v.length);
    }, 0);
    return { wch: Math.min(Math.max(headerLen, maxValLen) + 3, 40) };
  });
  worksheet['!cols'] = colWidths;

  const workbook = XLSX.utils.book_new();
  XLSX.utils.book_append_sheet(workbook, worksheet, 'AI Report');

  // Trigger download
  XLSX.writeFile(workbook, filename.endsWith('.xlsx') ? filename : `${filename}.xlsx`);
}

export function exportToCsv(
  data: Record<string, any>[],
  columns: ColumnDef[],
  filename = 'erp-ai-report.csv'
): void {
  if (!data || data.length === 0) return;

  const headers = columns.map((col) => `"${(col.label || col.key).replace(/"/g, '""')}"`);
  const rows = data.map((row) =>
    columns
      .map((col) => {
        const val = row[col.key];
        if (val === null || val === undefined) return '""';
        return `"${String(val).replace(/"/g, '""')}"`;
      })
      .join(',')
  );

  const csvContent = '\uFEFF' + [headers.join(','), ...rows].join('\r\n');
  const blob = new Blob([csvContent], { type: 'text/csv;charset=utf-8;' });
  const url = URL.createObjectURL(blob);

  const link = document.createElement('a');
  link.setAttribute('href', url);
  link.setAttribute('download', filename.endsWith('.csv') ? filename : `${filename}.csv`);
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  URL.revokeObjectURL(url);
}
