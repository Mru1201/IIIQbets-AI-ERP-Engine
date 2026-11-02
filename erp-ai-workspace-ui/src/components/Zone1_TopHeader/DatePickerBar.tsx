import React, { useState } from 'react';
import { Calendar, Check } from 'lucide-react';

export type DatePreset = 'Today' | 'MTD' | 'Q1' | 'Q2' | 'Q3' | 'Q4' | 'YTD' | 'Custom';

export interface DatePickerBarProps {
  onDateChange: (fromDate: string, toDate: string, preset: DatePreset) => void;
  activePreset?: DatePreset;
  referenceDate?: string | Date;
}

const getReferenceParts = (ref?: string | Date) => {
  if (!ref) {
    const d = new Date();
    return { y: d.getFullYear(), m: d.getMonth() + 1, d: d.getDate() };
  }
  if (typeof ref === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(ref)) {
    const [y, m, d] = ref.split('-').map(Number);
    return { y, m, d };
  }
  const parsed = new Date(ref);
  return { y: parsed.getFullYear(), m: parsed.getMonth() + 1, d: parsed.getDate() };
};

export const DatePickerBar: React.FC<DatePickerBarProps> = ({
  onDateChange,
  activePreset = 'YTD',
  referenceDate,
}) => {
  const [selectedPreset, setSelectedPreset] = useState<DatePreset>(activePreset);
  const initialParts = getReferenceParts(referenceDate);
  const pad = (n: number) => n.toString().padStart(2, '0');
  const [customFrom, setCustomFrom] = useState(`${initialParts.y}-01-01`);
  const [customTo, setCustomTo] = useState(`${initialParts.y}-12-31`);

  const presets: DatePreset[] = ['Today', 'MTD', 'Q1', 'Q2', 'Q3', 'Q4', 'YTD'];

  const computeDates = (preset: DatePreset) => {
    const { y, m, d } = getReferenceParts(referenceDate);
    const todayStr = `${y}-${pad(m)}-${pad(d)}`;
    let from = `${y}-01-01`;
    let to = `${y}-12-31`;

    switch (preset) {
      case 'Today':
        from = todayStr;
        to = todayStr;
        break;
      case 'MTD':
        from = `${y}-${pad(m)}-01`;
        to = todayStr;
        break;
      case 'Q1':
        from = `${y}-01-01`;
        to = `${y}-03-31`;
        break;
      case 'Q2':
        from = `${y}-04-01`;
        to = `${y}-06-30`;
        break;
      case 'Q3':
        from = `${y}-07-01`;
        to = `${y}-09-30`;
        break;
      case 'Q4':
        from = `${y}-10-01`;
        to = `${y}-12-31`;
        break;
      case 'YTD':
      default:
        from = `${y}-01-01`;
        to = todayStr;
        break;
    }

    setCustomFrom(from);
    setCustomTo(to);
    setSelectedPreset(preset);
    onDateChange(from, to, preset);
  };

  const handleApplyCustom = () => {
    setSelectedPreset('Custom');
    onDateChange(customFrom, customTo, 'Custom');
  };

  return (
    <div className="flex flex-wrap items-center justify-between gap-3 bg-white p-2.5 rounded-lg border border-fiori-border mb-4 shadow-2xs">
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="text-xs font-semibold text-fiori-gray flex items-center mr-1">
          <Calendar className="w-3.5 h-3.5 mr-1 text-fiori-blue" />
          Period:
        </span>
        {presets.map((p) => {
          const isActive = selectedPreset === p;
          return (
            <button
              key={p}
              onClick={() => computeDates(p)}
              className={`text-xs px-2.5 py-1 rounded-md font-medium transition-colors ${
                isActive
                  ? 'bg-fiori-blue text-white shadow-2xs'
                  : 'bg-slate-100 text-slate-700 hover:bg-slate-200'
              }`}
            >
              {p}
            </button>
          );
        })}
      </div>

      <div className="flex items-center space-x-2 text-xs">
        <div className="flex items-center space-x-1">
          <span className="text-slate-500">From:</span>
          <input
            type="date"
            value={customFrom}
            onChange={(e) => setCustomFrom(e.target.value)}
            className="border border-slate-300 rounded px-2 py-0.5 text-xs text-slate-800 bg-slate-50 focus:bg-white focus:outline-hidden focus:border-fiori-blue"
          />
        </div>
        <div className="flex items-center space-x-1">
          <span className="text-slate-500">To:</span>
          <input
            type="date"
            value={customTo}
            onChange={(e) => setCustomTo(e.target.value)}
            className="border border-slate-300 rounded px-2 py-0.5 text-xs text-slate-800 bg-slate-50 focus:bg-white focus:outline-hidden focus:border-fiori-blue"
          />
        </div>
        <button
          onClick={handleApplyCustom}
          className="inline-flex items-center text-xs bg-slate-800 text-white px-2.5 py-1 rounded hover:bg-slate-700 transition-colors"
        >
          <Check className="w-3 h-3 mr-1" />
          Apply
        </button>
      </div>
    </div>
  );
};
