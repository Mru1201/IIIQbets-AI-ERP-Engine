import React from 'react';
import { Filter, X, RotateCcw } from 'lucide-react';
import { FilterItem } from '../../types/api';

interface FilterBarProps {
  filters: FilterItem[];
  onRemoveFilter: (key: string) => void;
  onClearAll: () => void;
}

export const FilterBar: React.FC<FilterBarProps> = ({
  filters,
  onRemoveFilter,
  onClearAll,
}) => {
  if (!filters || filters.length === 0) return null;

  return (
    <div className="flex flex-wrap items-center gap-2 mb-3 bg-white/70 backdrop-blur-sm p-2 rounded-lg border border-fiori-border/60">
      <div className="flex items-center text-xs font-semibold text-fiori-gray mr-1">
        <Filter className="w-3.5 h-3.5 mr-1.5 text-fiori-blue" />
        Active Filters:
      </div>

      {filters.map((f, idx) => (
        <span
          key={`${f.key}-${idx}`}
          className="inline-flex items-center text-xs font-medium bg-fiori-blue-light text-fiori-blue px-2.5 py-1 rounded-full border border-blue-200 transition-colors shadow-2xs"
        >
          <span className="font-semibold mr-1">{f.label || f.key}:</span>
          <span className="mr-1.5 text-slate-700">{String(f.value)}</span>
          <button
            onClick={() => onRemoveFilter(f.key)}
            className="text-fiori-blue hover:text-rose-600 focus:outline-hidden"
            title={`Remove ${f.label || f.key} filter`}
          >
            <X className="w-3 h-3" />
          </button>
        </span>
      ))}

      <button
        onClick={onClearAll}
        className="inline-flex items-center text-xs font-medium text-slate-500 hover:text-rose-600 ml-auto transition-colors px-2 py-0.5 rounded hover:bg-slate-100"
      >
        <RotateCcw className="w-3 h-3 mr-1" />
        Clear All
      </button>
    </div>
  );
};
