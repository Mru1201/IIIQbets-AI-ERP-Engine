import React, { useMemo, useState } from 'react';
import {
  useReactTable,
  getCoreRowModel,
  getSortedRowModel,
  getFilteredRowModel,
  getPaginationRowModel,
  ColumnDef as TanStackColumnDef,
  flexRender,
  SortingState,
} from '@tanstack/react-table';
import {
  ArrowUpDown,
  ArrowUp,
  ArrowDown,
  Search,
  Calculator,
  ChevronLeft,
  ChevronRight,
  ChevronsLeft,
  ChevronsRight,
} from 'lucide-react';
import { ColumnDef } from '../../types/api';
import { formatCurrency, formatNumber, formatDate } from '../../utils/formatters';
import { ColumnToggleMenu } from './ColumnToggleMenu';
import { ExportButtons } from './ExportButtons';

export interface HighDensityTableProps {
  data: Record<string, any>[];
  columns: ColumnDef[];
  onOpenFormula?: (metricKey: string) => void;
  activeFilterField?: string | null;
  activeFilterValue?: any;
  onExportTriggered?: (format: string) => void;
}

export const HighDensityTable: React.FC<HighDensityTableProps> = ({
  data,
  columns,
  onOpenFormula,
  activeFilterField,
  activeFilterValue,
  onExportTriggered,
}) => {
  const [sorting, setSorting] = useState<SortingState>([]);
  const [globalFilter, setGlobalFilter] = useState('');
  const [pageSize, setPageSize] = useState(25);

  // TanStack Table Column Definitions
  const tanstackColumns = useMemo<TanStackColumnDef<Record<string, any>>[]>(() => {
    return columns.map((col) => ({
      accessorKey: col.key,
      header: col.label,
      cell: (info) => {
        const val = info.getValue();
        if (val === null || val === undefined) return <span className="text-slate-300">-</span>;

        if (col.type === 'currency') {
          return (
            <span className="font-mono text-slate-800 font-medium">
              {formatCurrency(val as number)}
            </span>
          );
        }
        if (col.type === 'number') {
          return (
            <span className="font-mono text-slate-850">
              {formatNumber(val as number)}
            </span>
          );
        }
        if (col.type === 'date') {
          return (
            <span className="text-slate-600 font-mono text-xs">
              {formatDate(val as string)}
            </span>
          );
        }
        return <span className="text-slate-800 truncate">{String(val)}</span>;
      },
      meta: {
        align: col.align || (col.type === 'currency' || col.type === 'number' ? 'right' : 'left'),
        metricKey: col.metric_key,
      },
    }));
  }, [columns]);

  const table = useReactTable({
    data,
    columns: tanstackColumns,
    state: {
      sorting,
      globalFilter,
    },
    onSortingChange: setSorting,
    onGlobalFilterChange: setGlobalFilter,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
    getFilteredRowModel: getFilteredRowModel(),
    getPaginationRowModel: getPaginationRowModel(),
    initialState: {
      pagination: {
        pageSize: 25,
      },
    },
  });

  return (
    <div className="bg-white rounded-lg border border-fiori-border shadow-xs overflow-hidden">
      {/* Top Table Toolbar */}
      <div className="p-3 border-b border-slate-200 flex flex-wrap items-center justify-between gap-3 bg-slate-50/70">
        <div className="flex items-center space-x-2.5 flex-1 min-w-56">
          <div className="relative w-full max-w-xs">
            <Search className="w-3.5 h-3.5 absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-400" />
            <input
              type="text"
              value={globalFilter ?? ''}
              onChange={(e) => setGlobalFilter(e.target.value)}
              placeholder="Filter records..."
              className="w-full text-xs pl-8 pr-2.5 py-1.5 border border-slate-300 rounded bg-white text-slate-800 placeholder-slate-400 focus:outline-hidden focus:border-fiori-blue focus:ring-1 focus:ring-fiori-blue/30"
            />
          </div>
          <ColumnToggleMenu table={table} />
        </div>

        <div className="flex items-center space-x-3">
          <span className="text-xs text-slate-500 font-medium">
            {table.getFilteredRowModel().rows.length} of {data.length} records
          </span>
          <ExportButtons data={data} columns={columns} onExportTriggered={onExportTriggered} />
        </div>
      </div>

      {/* 32px Compact Enterprise Table */}
      <div className="overflow-x-auto">
        <table className="w-full border-collapse fiori-table-compact">
          <thead>
            {table.getHeaderGroups().map((headerGroup) => (
              <tr key={headerGroup.id} className="bg-slate-100/90 border-b border-slate-200">
                {headerGroup.headers.map((header) => {
                  const meta = header.column.columnDef.meta as { align?: string; metricKey?: string } | undefined;
                  const alignClass =
                    meta?.align === 'right' ? 'text-right' : meta?.align === 'center' ? 'text-center' : 'text-left';

                  return (
                    <th
                      key={header.id}
                      className={`text-xs font-bold text-slate-700 select-none ${alignClass} py-2 px-3 tracking-wider`}
                    >
                      <div
                        className={`inline-flex items-center space-x-1 ${
                          header.column.getCanSort() ? 'cursor-pointer hover:text-fiori-blue' : ''
                        }`}
                        onClick={header.column.getToggleSortingHandler()}
                      >
                        <span>{flexRender(header.column.columnDef.header, header.getContext())}</span>
                        {header.column.getCanSort() && (
                          <span className="text-slate-400 ml-1">
                            {header.column.getIsSorted() === 'asc' ? (
                              <ArrowUp className="w-3 h-3 text-fiori-blue" />
                            ) : header.column.getIsSorted() === 'desc' ? (
                              <ArrowDown className="w-3 h-3 text-fiori-blue" />
                            ) : (
                              <ArrowUpDown className="w-3 h-3" />
                            )}
                          </span>
                        )}

                        {meta?.metricKey && onOpenFormula && (
                          <button
                            onClick={(e) => {
                              e.stopPropagation();
                              onOpenFormula(meta.metricKey!);
                            }}
                            title="Inspect NetSuite fx Formula"
                            className="inline-flex items-center px-1 py-0.2 text-[9px] font-bold rounded bg-blue-50 text-fiori-blue border border-blue-200 hover:bg-blue-100 ml-1"
                          >
                            <Calculator className="w-2.5 h-2.5 mr-0.5" />
                            fx
                          </button>
                        )}
                      </div>
                    </th>
                  );
                })}
              </tr>
            ))}
          </thead>
          <tbody className="divide-y divide-slate-100">
            {table.getRowModel().rows.length === 0 ? (
              <tr>
                <td colSpan={columns.length} className="text-center py-8 text-xs text-slate-400">
                  No matching records found.
                </td>
              </tr>
            ) : (
              table.getRowModel().rows.map((row, idx) => {
                const isCrossFiltered =
                  activeFilterField &&
                  activeFilterValue &&
                  row.original[activeFilterField] === activeFilterValue;

                return (
                  <tr
                    key={row.id}
                    className={`transition-colors hover:bg-fiori-blue-light/20 ${
                      idx % 2 === 1 ? 'bg-slate-50/50' : 'bg-white'
                    } ${isCrossFiltered ? 'bg-blue-100/60 font-semibold ring-1 ring-fiori-blue/40' : ''}`}
                  >
                    {row.getVisibleCells().map((cell) => {
                      const meta = cell.column.columnDef.meta as { align?: string } | undefined;
                      const alignClass =
                        meta?.align === 'right' ? 'text-right' : meta?.align === 'center' ? 'text-center' : 'text-left';

                      return (
                        <td key={cell.id} className={`py-1.5 px-3 text-xs ${alignClass}`}>
                          {flexRender(cell.column.columnDef.cell, cell.getContext())}
                        </td>
                      );
                    })}
                  </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>

      {/* Pagination Footer */}
      <div className="p-3 border-t border-slate-200 flex flex-wrap items-center justify-between gap-3 text-xs text-slate-600 bg-white">
        <div className="flex items-center space-x-2">
          <span>Rows per page:</span>
          <select
            value={table.getState().pagination.pageSize}
            onChange={(e) => {
              const sz = Number(e.target.value);
              setPageSize(sz);
              table.setPageSize(sz);
            }}
            className="border border-slate-300 rounded px-2 py-0.5 text-xs bg-white text-slate-700"
          >
            {[10, 25, 50, 100].map((sz) => (
              <option key={sz} value={sz}>
                {sz}
              </option>
            ))}
          </select>
        </div>

        <div className="flex items-center space-x-3">
          <span>
            Page <span className="font-semibold">{table.getState().pagination.pageIndex + 1}</span> of{' '}
            <span className="font-semibold">{Math.max(table.getPageCount(), 1)}</span>
          </span>

          <div className="inline-flex items-center space-x-1">
            <button
              onClick={() => table.setPageIndex(0)}
              disabled={!table.getCanPreviousPage()}
              className="p-1 rounded border border-slate-200 disabled:opacity-30 disabled:cursor-not-allowed hover:bg-slate-100"
              title="First Page"
            >
              <ChevronsLeft className="w-3.5 h-3.5" />
            </button>
            <button
              onClick={() => table.previousPage()}
              disabled={!table.getCanPreviousPage()}
              className="p-1 rounded border border-slate-200 disabled:opacity-30 disabled:cursor-not-allowed hover:bg-slate-100"
              title="Previous Page"
            >
              <ChevronLeft className="w-3.5 h-3.5" />
            </button>
            <button
              onClick={() => table.nextPage()}
              disabled={!table.getCanNextPage()}
              className="p-1 rounded border border-slate-200 disabled:opacity-30 disabled:cursor-not-allowed hover:bg-slate-100"
              title="Next Page"
            >
              <ChevronRight className="w-3.5 h-3.5" />
            </button>
            <button
              onClick={() => table.setPageIndex(table.getPageCount() - 1)}
              disabled={!table.getCanNextPage()}
              className="p-1 rounded border border-slate-200 disabled:opacity-30 disabled:cursor-not-allowed hover:bg-slate-100"
              title="Last Page"
            >
              <ChevronsRight className="w-3.5 h-3.5" />
            </button>
          </div>
        </div>
      </div>
    </div>
  );
};
