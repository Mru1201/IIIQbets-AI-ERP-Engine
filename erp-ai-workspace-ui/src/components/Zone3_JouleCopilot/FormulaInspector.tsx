import React from 'react';
import { Calculator, X, Database, FileCode, Copy, Check } from 'lucide-react';
import { CANONICAL_METRICS } from '../../api/client';
import { MetricDefinition } from '../../types/api';

interface FormulaInspectorProps {
  metricKey: string | null;
  onClose: () => void;
}

export const FormulaInspector: React.FC<FormulaInspectorProps> = ({
  metricKey,
  onClose,
}) => {
  const [copied, setCopied] = React.useState(false);

  if (!metricKey) return null;

  const metric: MetricDefinition = CANONICAL_METRICS[metricKey] || {
    key: metricKey,
    label: metricKey.replace(/_/g, ' ').toUpperCase(),
    canonical_sql: `SELECT ${metricKey} FROM ...`,
    required_tables: ['voucher'],
    description: `Calculated enterprise metric: ${metricKey}`,
    source_reference: 'metastore/calculated_metrics',
  };

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(metric.canonical_sql);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // Fallback
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 backdrop-blur-xs p-4">
      <div className="bg-white rounded-xl shadow-2xl border border-slate-200 w-full max-w-lg overflow-hidden animate-in fade-in zoom-in-95 duration-150">
        {/* Header */}
        <div className="flex items-center justify-between px-4 py-3 bg-slate-50 border-b border-slate-200">
          <div className="flex items-center space-x-2">
            <span className="p-1 rounded bg-blue-100 text-fiori-blue">
              <Calculator className="w-4 h-4" />
            </span>
            <div>
              <h3 className="text-sm font-bold text-slate-800">
                Formula Inspector: {metric.label}
              </h3>
              <span className="text-[11px] font-mono text-slate-500">
                Key: {metric.key}
              </span>
            </div>
          </div>
          <button
            onClick={onClose}
            className="p-1 rounded-md text-slate-400 hover:text-slate-600 hover:bg-slate-200 transition-colors"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        {/* Body */}
        <div className="p-4 space-y-3.5 text-xs">
          <div>
            <span className="font-semibold text-slate-700 block mb-1">
              Business Definition:
            </span>
            <p className="text-slate-600 bg-slate-50 p-2.5 rounded border border-slate-200/60 leading-relaxed">
              {metric.description}
            </p>
          </div>

          <div>
            <div className="flex items-center justify-between mb-1">
              <span className="font-semibold text-slate-700">
                Canonical SQL Expression:
              </span>
              <button
                onClick={handleCopy}
                className="inline-flex items-center text-[11px] text-fiori-blue hover:underline"
              >
                {copied ? (
                  <>
                    <Check className="w-3 h-3 mr-1 text-emerald-600" />
                    Copied
                  </>
                ) : (
                  <>
                    <Copy className="w-3 h-3 mr-1" />
                    Copy Expression
                  </>
                )}
              </button>
            </div>
            <pre className="p-2.5 rounded bg-slate-900 text-emerald-300 font-mono text-[11px] overflow-x-auto whitespace-pre-wrap max-h-36 border border-slate-800 select-all">
              {metric.canonical_sql}
            </pre>
          </div>

          <div className="grid grid-cols-2 gap-3 pt-1">
            <div className="bg-slate-50 p-2 rounded border border-slate-200">
              <span className="font-semibold text-slate-700 flex items-center mb-1 text-[11px]">
                <Database className="w-3 h-3 mr-1 text-fiori-blue" />
                Required Tables:
              </span>
              <div className="flex flex-wrap gap-1">
                {metric.required_tables.map((tbl) => (
                  <span
                    key={tbl}
                    className="px-1.5 py-0.5 rounded bg-white border border-slate-200 text-slate-700 font-mono text-[10px]"
                  >
                    {tbl}
                  </span>
                ))}
              </div>
            </div>

            <div className="bg-slate-50 p-2 rounded border border-slate-200">
              <span className="font-semibold text-slate-700 flex items-center mb-1 text-[11px]">
                <FileCode className="w-3 h-3 mr-1 text-fiori-blue" />
                Metastore Reference:
              </span>
              <span
                className="font-mono text-[10px] text-slate-600 break-all block"
                title={metric.source_reference}
              >
                {metric.source_reference || 'Metastore Schema v1.0'}
              </span>
            </div>
          </div>
        </div>

        {/* Footer */}
        <div className="px-4 py-2.5 bg-slate-50 border-t border-slate-200 flex justify-end">
          <button
            onClick={onClose}
            className="px-3 py-1.5 bg-slate-800 hover:bg-slate-900 text-white font-medium rounded text-xs transition-colors"
          >
            Close
          </button>
        </div>
      </div>
    </div>
  );
};
