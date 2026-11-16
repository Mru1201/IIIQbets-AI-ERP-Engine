import React, { useState, useEffect } from 'react';
import {
  Search,
  LayoutGrid,
  BarChart2,
  Table as TableIcon,
  Bot,
  RefreshCw,
  Sparkles,
} from 'lucide-react';
import { ReportQueryResponse, FilterItem, KPICard } from '../types/api';
import { queryReport, generateMockReport } from '../api/client';
import { KPIHeader } from './Zone1_TopHeader/KPIHeader';
import { FilterBar } from './Zone1_TopHeader/FilterBar';
import { DatePickerBar, DatePreset } from './Zone1_TopHeader/DatePickerBar';
import { ExecutiveNarrative } from './Zone1_TopHeader/ExecutiveNarrative';
import { ChartCanvas } from './Zone2_CenterCanvas/ChartCanvas';
import { HighDensityTable } from './Zone2_CenterCanvas/HighDensityTable';
import { JouleDrawer } from './Zone3_JouleCopilot/JouleDrawer';
import { FormulaInspector } from './Zone3_JouleCopilot/FormulaInspector';
import { AwesomebarModal } from './Awesomebar/AwesomebarModal';
import { InvoiceCapturePage } from './InvoiceCapture/InvoiceCapturePage';

export interface AnalyticalListPageProps {
  apiUrl?: string;
  captureApiUrl?: string;
  tenantId?: string;
  token?: string;
  defaultQuery?: string;
  devMode?: boolean;
  theme?: string;
  referenceDate?: string | Date;
  onReportGenerated?: (data: ReportQueryResponse) => void;
  onFormulaInspected?: (metricKey: string) => void;
  onExportTriggered?: (format: string) => void;
  onFilterChanged?: (filters: any) => void;
}

export const AnalyticalListPage: React.FC<AnalyticalListPageProps> = ({
  apiUrl = 'http://localhost:8000',
  captureApiUrl = 'http://localhost:8010',
  tenantId = 'shree_shashwatraj_agro',
  token,
  defaultQuery = 'What is our net profit and gross profit for 2026?',
  devMode = false,
  theme,
  referenceDate,
  onReportGenerated,
  onFormulaInspected,
  onExportTriggered,
  onFilterChanged,
}) => {
  const [reportData, setReportData] = useState<ReportQueryResponse | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [currentQuery, setCurrentQuery] = useState(defaultQuery);

  // Layout View Modes
  const [viewMode, setViewMode] = useState<'split' | 'chart' | 'table'>('split');
  const [isJouleOpen, setIsJouleOpen] = useState(true);
  const [isAwesomebarOpen, setIsAwesomebarOpen] = useState(false);

  // Cross-filtering and formula inspector state
  const [activeFilterField, setActiveFilterField] = useState<string | null>(null);
  const [activeFilterValue, setActiveFilterValue] = useState<any>(null);
  const [activeFilters, setActiveFilters] = useState<FilterItem[]>([]);
  const [inspectingMetricKey, setInspectingMetricKey] = useState<string | null>(null);
  const [activeVertical, setActiveVertical] = useState<'reporting' | 'capture'>('reporting');

  // Global Cmd+K / Ctrl+K keyboard shortcut to toggle Awesomebar
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault();
        setIsAwesomebarOpen((prev) => !prev);
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, []);

  const handleInspectFormula = (key: string | null) => {
    setInspectingMetricKey(key);
    if (key) {
      onFormulaInspected?.(key);
    }
  };

  const executeQuery = async (queryText: string, params?: Record<string, any>) => {
    setIsLoading(true);
    setCurrentQuery(queryText);
    try {
      const resp = await queryReport(
        {
          tenant_id: tenantId,
          user_query: queryText,
          parameters: params,
        },
        token,
        apiUrl
      );
      setReportData(resp);
      const filters = resp.ui_schema?.filters || [];
      setActiveFilters(filters);
      // Reset cross filter
      setActiveFilterField(null);
      setActiveFilterValue(null);
      onReportGenerated?.(resp);
      onFilterChanged?.(filters);
    } catch (err) {
      console.error('Failed to execute report query:', err);
      // Fallback
      const mock = generateMockReport({ tenant_id: tenantId, user_query: queryText });
      setReportData(mock);
      setActiveFilters(mock.ui_schema.filters);
      onReportGenerated?.(mock);
      onFilterChanged?.(mock.ui_schema.filters);
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    executeQuery(defaultQuery);
  }, []);

  // Cross-filter click handler from ChartCanvas
  const handleCrossFilter = (field: string, value: any) => {
    if (activeFilterField === field && activeFilterValue === value) {
      // Toggle off
      setActiveFilterField(null);
      setActiveFilterValue(null);
      onFilterChanged?.({ field: null, value: null, activeFilters });
    } else {
      setActiveFilterField(field);
      setActiveFilterValue(value);
      onFilterChanged?.({ field, value, activeFilters });
    }
  };

  const handleRemoveFilter = (key: string) => {
    setActiveFilters((prev) => {
      const next = prev.filter((f) => f.key !== key);
      onFilterChanged?.(next);
      return next;
    });
    if (activeFilterField === key) {
      setActiveFilterField(null);
      setActiveFilterValue(null);
    }
  };

  const handleClearAllFilters = () => {
    setActiveFilters([]);
    onFilterChanged?.([]);
    setActiveFilterField(null);
    setActiveFilterValue(null);
  };

  const handleDateChange = (from: string, to: string, preset: DatePreset) => {
    executeQuery(`${currentQuery} from ${from} to ${to}`, { from_date: from, to_date: to, preset });
  };

  const handleKpiSelect = (kpi: KPICard) => {
    if (kpi.metric_key) {
      handleInspectFormula(kpi.metric_key);
    }
  };

  return (
    <div className="min-h-screen bg-[#F5F6F7] text-fiori-dark flex flex-col font-sans" data-theme={theme}>
      {/* Top Application Header */}
      <header className="min-h-14 bg-white border-b border-fiori-border px-4 py-2 flex flex-wrap items-center justify-between gap-3 sticky top-0 z-40 shadow-xs">
        <div className="flex min-w-0 flex-1 items-center gap-3">
          <div className="flex min-w-0 flex-wrap items-center gap-3">
            <div className="min-w-0">
              <h1 className="text-sm font-bold text-fiori-dark leading-tight">
                AI-Reporting Workspace
              </h1>
              <span className="block max-w-[18rem] truncate text-[11px] text-slate-500 font-medium">
                Tenant: <span className="font-mono text-slate-700">{tenantId}</span>
              </span>
            </div>
            <div className="inline-flex rounded-md border border-slate-200 bg-slate-100 p-0.5 text-xs">
              <button
                type="button"
                onClick={() => setActiveVertical('reporting')}
                className={`rounded px-2.5 py-1.5 ${activeVertical === 'reporting' ? 'bg-white font-semibold text-fiori-blue shadow-2xs' : 'text-slate-600'}`}
              >
                AI Reporting
              </button>
              <button
                type="button"
                onClick={() => setActiveVertical('capture')}
                className={`rounded px-2.5 py-1.5 ${activeVertical === 'capture' ? 'bg-white font-semibold text-fiori-blue shadow-2xs' : 'text-slate-600'}`}
              >
                Invoice Capture
              </button>
            </div>
          </div>
        </div>

        {/* Global Awesomebar Cmd+K Search Trigger */}
        <div className="ml-auto flex max-w-full flex-wrap items-center justify-end gap-2">
          <button
            onClick={() => setIsAwesomebarOpen(true)}
            className="flex min-w-0 max-w-full items-center justify-between rounded-lg border border-slate-300/80 bg-slate-100 px-3 py-1.5 text-xs text-slate-400 shadow-2xs transition-colors hover:bg-slate-200/80 group w-[min(28rem,42vw)]"
          >
            <span className="flex items-center text-slate-600 truncate mr-2 font-medium">
              <Search className="w-3.5 h-3.5 mr-2 text-fiori-blue" />
              {currentQuery}
            </span>
            <kbd className="px-1.5 py-0.5 text-[10px] bg-white border border-slate-300 rounded text-slate-500 font-mono shadow-2xs group-hover:border-slate-400">
              ⌘K
            </kbd>
          </button>

          {activeVertical === 'reporting' && (
            <>
          {/* View Mode Switcher */}
          <div className="inline-flex items-center bg-slate-100 p-0.5 rounded-md border border-slate-200 text-xs">
            <button
              onClick={() => setViewMode('split')}
              className={`p-1.5 rounded transition-colors ${
                viewMode === 'split' ? 'bg-white font-semibold text-fiori-blue shadow-2xs' : 'text-slate-600 hover:text-slate-900'
              }`}
              title="Split View (Chart + Table)"
            >
              <LayoutGrid className="w-3.5 h-3.5" />
            </button>
            <button
              onClick={() => setViewMode('chart')}
              className={`p-1.5 rounded transition-colors ${
                viewMode === 'chart' ? 'bg-white font-semibold text-fiori-blue shadow-2xs' : 'text-slate-600 hover:text-slate-900'
              }`}
              title="Chart View Only"
            >
              <BarChart2 className="w-3.5 h-3.5" />
            </button>
            <button
              onClick={() => setViewMode('table')}
              className={`p-1.5 rounded transition-colors ${
                viewMode === 'table' ? 'bg-white font-semibold text-fiori-blue shadow-2xs' : 'text-slate-600 hover:text-slate-900'
              }`}
              title="Table Grid Only"
            >
              <TableIcon className="w-3.5 h-3.5" />
            </button>
          </div>

          {/* Reporting Copilot Drawer Toggle */}
          <button
            onClick={() => setIsJouleOpen(!isJouleOpen)}
            className={`inline-flex items-center text-xs font-semibold px-2.5 py-1.5 rounded transition-colors ${
              isJouleOpen
                ? 'bg-fiori-blue text-white shadow-2xs'
                : 'bg-white border border-slate-300 text-slate-700 hover:bg-slate-50'
            }`}
            title="Toggle Reporting Copilot Drawer"
          >
            <Bot className="w-3.5 h-3.5 mr-1.5" />
            Copilot
          </button>
            </>
          )}
        </div>
      </header>

      {activeVertical === 'capture' ? (
        <InvoiceCapturePage tenantId={tenantId} token={token} captureApiUrl={captureApiUrl} />
      ) : (
      /* Main Workspace 3-Zone Layout */
      <div className="flex min-h-0 flex-1 flex-col overflow-hidden lg:flex-row">
        {/* Left Canvas: Zone 1 & Zone 2 */}
        <main className="min-w-0 flex-1 overflow-y-auto p-4 transition-all lg:p-6">
          {isLoading && (
            <div className="flex items-center justify-center p-3 mb-4 rounded-lg bg-blue-50/80 border border-blue-200 text-xs font-medium text-fiori-blue animate-pulse">
              <RefreshCw className="w-4 h-4 mr-2 animate-spin" />
              Synthesizing schema, executing AST-verified query, and deriving visualizations...
            </div>
          )}

          {reportData && (
            <>
              {/* ZONE 1: TOP HEADER & VISUAL FILTER BAR */}
              <section aria-label="Zone 1: Executive KPI Summary and Filters">
                <KPIHeader
                  kpis={reportData.ui_schema?.kpis || []}
                  onSelectKpi={handleKpiSelect}
                  onOpenFormula={handleInspectFormula}
                />

                <FilterBar
                  filters={activeFilters}
                  onRemoveFilter={handleRemoveFilter}
                  onClearAll={handleClearAllFilters}
                />

                <DatePickerBar onDateChange={handleDateChange} referenceDate={referenceDate} />

                <ExecutiveNarrative
                  thoughtProcess={reportData.execution_metadata?.thought_process}
                  onOpenJoule={() => setIsJouleOpen(true)}
                />
              </section>

              {/* ZONE 2: CENTER HYBRID CANVAS & HIGH-DENSITY GRID */}
              <section aria-label="Zone 2: Data Visualizations and Compact Grid">
                {(viewMode === 'split' || viewMode === 'chart') && (
                  <ChartCanvas
                    data={reportData.data || []}
                    columns={reportData.ui_schema?.columns || []}
                    suggestedVisualization={reportData.ui_schema?.suggested_visualization || 'bar_chart'}
                    onCrossFilter={handleCrossFilter}
                    activeFilterValue={activeFilterValue}
                  />
                )}

                {(viewMode === 'split' || viewMode === 'table') && (
                  <HighDensityTable
                    data={reportData.data || []}
                    columns={reportData.ui_schema?.columns || []}
                    onOpenFormula={handleInspectFormula}
                    activeFilterField={activeFilterField}
                    activeFilterValue={activeFilterValue}
                    onExportTriggered={onExportTriggered}
                  />
                )}
              </section>
            </>
          )}
        </main>

        {/* ZONE 3: RIGHT DOCKED JOULE COPILOT DRAWER (380px) */}
        <JouleDrawer
          isOpen={isJouleOpen}
          onToggle={() => setIsJouleOpen(!isJouleOpen)}
          metadata={reportData?.execution_metadata}
          rowCount={reportData?.data?.length || 0}
          onSubmitQuery={executeQuery}
          isLoading={isLoading}
        />
      </div>
      )}

      {/* Global NetSuite fx Formula Inspector Popover Modal */}
      <FormulaInspector
        metricKey={inspectingMetricKey}
        onClose={() => handleInspectFormula(null)}
      />

      {/* Global Cmd+K Awesomebar Command Palette */}
      <AwesomebarModal
        isOpen={isAwesomebarOpen}
        onClose={() => setIsAwesomebarOpen(false)}
        onSelectPrompt={executeQuery}
      />
    </div>
  );
};
