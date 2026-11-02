import React, { useState } from 'react';
import { Send, Sparkles } from 'lucide-react';

interface MultiTurnInputProps {
  onSubmitQuery: (prompt: string) => void;
  isLoading?: boolean;
}

const STARTER_SUGGESTIONS = [
  'Show top 5 only',
  'Break down by month',
  'Compare against last year',
  'Exclude cancelled vouchers',
  'Filter for outstanding > ₹20,000',
];

export const MultiTurnInput: React.FC<MultiTurnInputProps> = ({
  onSubmitQuery,
  isLoading = false,
}) => {
  const [inputVal, setInputVal] = useState('');

  const handleSubmit = (e?: React.FormEvent) => {
    e?.preventDefault();
    if (!inputVal.trim() || isLoading) return;
    onSubmitQuery(inputVal.trim());
    setInputVal('');
  };

  return (
    <div className="border-t border-slate-200 pt-3 bg-white">
      {/* Suggestion Chips */}
      <div className="flex items-center space-x-1.5 overflow-x-auto pb-2 scrollbar-none mb-2">
        <Sparkles className="w-3 h-3 text-fiori-blue shrink-0 ml-0.5" />
        {STARTER_SUGGESTIONS.map((chip) => (
          <button
            key={chip}
            onClick={() => onSubmitQuery(chip)}
            disabled={isLoading}
            className="text-[11px] px-2 py-0.5 rounded-full bg-slate-100 hover:bg-fiori-blue-light text-slate-700 hover:text-fiori-blue border border-slate-200/80 whitespace-nowrap transition-colors"
          >
            {chip}
          </button>
        ))}
      </div>

      <form onSubmit={handleSubmit} className="relative">
        <input
          type="text"
          value={inputVal}
          onChange={(e) => setInputVal(e.target.value)}
          placeholder="Refine or ask follow-up question..."
          disabled={isLoading}
          className="w-full text-xs pl-3 pr-10 py-2.5 rounded-lg border border-slate-300 focus:outline-hidden focus:border-fiori-blue focus:ring-1 focus:ring-fiori-blue/40 text-slate-800 placeholder-slate-400 bg-slate-50/50"
        />
        <button
          type="submit"
          disabled={!inputVal.trim() || isLoading}
          className="absolute right-1.5 top-1/2 -translate-y-1/2 p-1.5 rounded-md bg-fiori-blue text-white disabled:opacity-40 disabled:cursor-not-allowed hover:bg-fiori-blue-hover transition-colors"
          title="Submit follow-up prompt"
        >
          <Send className="w-3.5 h-3.5" />
        </button>
      </form>
    </div>
  );
};
