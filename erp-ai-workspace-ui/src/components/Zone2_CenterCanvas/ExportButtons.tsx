import React from 'react';
import { FileSpreadsheet, FileText } from 'lucide-react';
import { ColumnDef } from '../../types/api';
import { exportToExcel, exportToCsv } from '../../utils/exportHelper';

export interface ExportButtonsProps {
  data: Record<string, any>[];
  columns: ColumnDef[];
  reportTitle?: string;
  onExportTriggered?: (format: string) => void;
}

export const ExportButtons: React.FC<ExportButtonsProps> = ({
  data,
  columns,
  reportTitle = 'AI_ERP_Report',
  onExportTriggered,
}) => {
  const cleanTitle = reportTitle.toLowerCase().replace(/[^a-z0-9_-]/g, '_');

  const handleExportExcel = () => {
    exportToExcel(data, columns, `${cleanTitle}.xlsx`);
    onExportTriggered?.('xlsx');
  };

  const handleExportCsv = () => {
    exportToCsv(data, columns, `${cleanTitle}.csv`);
    onExportTriggered?.('csv');
  };

  return (
    <div className="inline-flex items-center space-x-1.5">
      <button
        onClick={handleExportExcel}
        title="Export styled Excel workbook (.xlsx)"
        className="inline-flex items-center text-xs font-semibold px-2.5 py-1.5 bg-emerald-700 hover:bg-emerald-800 text-white rounded shadow-2xs transition-colors"
      >
        <FileSpreadsheet className="w-3.5 h-3.5 mr-1.5" />
        Excel (.xlsx)
      </button>

      <button
        onClick={handleExportCsv}
        title="Export CSV data file (.csv)"
        className="inline-flex items-center text-xs font-semibold px-2.5 py-1.5 bg-slate-700 hover:bg-slate-800 text-white rounded shadow-2xs transition-colors"
      >
        <FileText className="w-3.5 h-3.5 mr-1.5" />
        CSV
      </button>
    </div>
  );
};
