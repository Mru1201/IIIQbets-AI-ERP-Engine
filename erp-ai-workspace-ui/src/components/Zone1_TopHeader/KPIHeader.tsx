import React from 'react';
import { TrendingUp, TrendingDown, Minus, Calculator } from 'lucide-react';
import { KPICard } from '../../types/api';
import { formatCurrency, formatNumber } from '../../utils/formatters';

interface KPIHeaderProps {
  kpis: KPICard[];
  selectedMetric?: string | null;
  onSelectKpi?: (kpi: KPICard) => void;
  onOpenFormula?: (metricKey: string) => void;
}

export const KPIHeader: React.FC<KPIHeaderProps> = ({
  kpis,
  selectedMetric,
  onSelectKpi,
  onOpenFormula,
}) => {
  if (!kpis || kpis.length === 0) return null;

  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4 mb-4">
      {kpis.map((kpi, idx) => {
        const isSelected = selectedMetric === kpi.label;
        const trend = kpi.trend ?? 'neutral';

        // Format value cleanly
        let displayVal = String(kpi.value ?? '');
        if (typeof kpi.value === 'number') {
          displayVal = formatNumber(kpi.value);
        } else if (displayVal.includes('INR')) {
          const num = parseFloat(displayVal.replace(/[^0-9.-]/g, ''));
          if (!isNaN(num)) displayVal = formatCurrency(num);
        }

        return (
          <div
            key={`${kpi.label}-${idx}`}
            onClick={() => onSelectKpi?.(kpi)}
            className={`relative bg-white rounded-lg p-4 border transition-all duration-150 cursor-pointer shadow-sm hover:shadow-md ${
              isSelected
                ? 'border-fiori-blue ring-2 ring-fiori-blue/20 bg-fiori-blue-light/10'
                : 'border-fiori-border hover:border-fiori-blue/40'
            }`}
          >
            <div className="flex items-center justify-between mb-2">
              <span className="text-xs font-medium text-fiori-gray uppercase tracking-wider truncate mr-2">
                {kpi.label}
              </span>

              <div className="flex items-center space-x-1.5">
                {kpi.metric_key && onOpenFormula && (
                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      onOpenFormula(kpi.metric_key!);
                    }}
                    title="Inspect NetSuite fx Formula"
                    className="inline-flex items-center px-1.5 py-0.5 text-[10px] font-bold rounded bg-blue-50 text-fiori-blue border border-blue-200 hover:bg-blue-100 transition-colors"
                  >
                    <Calculator className="w-2.5 h-2.5 mr-0.5" />
                    fx
                  </button>
                )}

                {trend === 'positive' && (
                  <span className="inline-flex items-center text-xs font-semibold px-2 py-0.5 rounded-full bg-emerald-50 text-emerald-700 border border-emerald-200">
                    <TrendingUp className="w-3 h-3 mr-1" />
                    Up
                  </span>
                )}
                {trend === 'negative' && (
                  <span className="inline-flex items-center text-xs font-semibold px-2 py-0.5 rounded-full bg-rose-50 text-rose-700 border border-rose-200">
                    <TrendingDown className="w-3 h-3 mr-1" />
                    Down
                  </span>
                )}
                {trend === 'neutral' && (
                  <span className="inline-flex items-center text-xs font-semibold px-2 py-0.5 rounded-full bg-slate-50 text-slate-600 border border-slate-200">
                    <Minus className="w-3 h-3 mr-1" />
                    Neutral
                  </span>
                )}
              </div>
            </div>

            <div className="text-2xl font-bold text-fiori-dark tracking-tight truncate">
              {displayVal}
            </div>

            {kpi.subtext && (
              <div className="mt-1 text-xs text-slate-500 truncate" title={kpi.subtext}>
                {kpi.subtext}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
};
