import React, { useState, useEffect, useRef } from 'react';
import { Search, Sparkles, X, Clock, ArrowRight, CornerDownLeft } from 'lucide-react';

interface AwesomebarModalProps {
  isOpen: boolean;
  onClose: () => void;
  onSelectPrompt: (prompt: string) => void;
}

interface PromptCategory {
  category: string;
  prompts: string[];
}

const CATEGORIZED_PROMPTS: PromptCategory[] = [
  {
    category: 'Sales & Revenue',
    prompts: [
      'Show top 10 customers by gross revenue in 2026',
      'Monthly sales revenue trend with invoice counts',
      'Compare sales performance between Q1 and Q2',
    ],
  },
  {
    category: 'Debtors & Receivables',
    prompts: [
      'List all outstanding debtors with credit limits',
      'Parties with unpaid balance exceeding ₹25,000',
      'Debtor aging and overdue receivables summary',
    ],
  },
  {
    category: 'Inventory & Stock',
    prompts: [
      'Show batch stock on hand and valuation',
      'Batches expiring in the next 90 days',
      'Top 5 products by quantity sold',
    ],
  },
  {
    category: 'Expenses & P&L',
    prompts: [
      'Calculate Gross Profit, COGS, and Net Profit for 2026',
      'Operating expenses breakdown by category',
      'Show profit margin percentage by retailer',
    ],
  },
  {
    category: 'GST & Compliance',
    prompts: [
      'GST tax breakdown: CGST, SGST, and IGST by month',
      'Total taxable sales vs tax collected',
    ],
  },
];

export const AwesomebarModal: React.FC<AwesomebarModalProps> = ({
  isOpen,
  onClose,
  onSelectPrompt,
}) => {
  const [searchTerm, setSearchTerm] = useState('');
  const [recentQueries, setRecentQueries] = useState<string[]>([]);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    try {
      const saved = localStorage.getItem('erp_ai_recent_queries');
      if (saved) setRecentQueries(JSON.parse(saved));
    } catch {
      // Ignore
    }
  }, []);

  useEffect(() => {
    if (isOpen) {
      setTimeout(() => inputRef.current?.focus(), 50);
    }
  }, [isOpen]);

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && isOpen) {
        onClose();
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [isOpen, onClose]);

  const handleExecute = (prompt: string) => {
    const trimmed = prompt.trim();
    if (!trimmed) return;

    // Save to recents
    const updated = [trimmed, ...recentQueries.filter((q) => q !== trimmed)].slice(0, 5);
    setRecentQueries(updated);
    try {
      localStorage.setItem('erp_ai_recent_queries', JSON.stringify(updated));
    } catch {
      // Ignore
    }

    onSelectPrompt(trimmed);
    onClose();
  };

  if (!isOpen) return null;

  const filteredCategories = CATEGORIZED_PROMPTS.map((cat) => ({
    ...cat,
    prompts: cat.prompts.filter((p) =>
      p.toLowerCase().includes(searchTerm.toLowerCase())
    ),
  })).filter((cat) => cat.prompts.length > 0);

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center pt-20 px-4 bg-black/50 backdrop-blur-xs animate-in fade-in duration-100">
      <div
        className="bg-white rounded-xl shadow-2xl border border-slate-200 w-full max-w-2xl overflow-hidden"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Awesomebar Input */}
        <div className="relative border-b border-slate-200 p-3.5 flex items-center bg-slate-50/60">
          <Search className="w-5 h-5 text-fiori-blue shrink-0 mr-3" />
          <input
            ref={inputRef}
            type="text"
            value={searchTerm}
            onChange={(e) => setSearchTerm(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && searchTerm.trim()) {
                handleExecute(searchTerm);
              }
            }}
            placeholder="Ask anything... (e.g. 'Show gross profit by retailer in Q3 2026')"
            className="w-full text-sm bg-transparent border-none focus:outline-hidden text-slate-800 placeholder-slate-400 font-medium"
          />

          {searchTerm && (
            <button
              onClick={() => setSearchTerm('')}
              className="p-1 text-slate-400 hover:text-slate-600 rounded mr-2"
            >
              <X className="w-4 h-4" />
            </button>
          )}

          <div className="flex items-center space-x-1.5 shrink-0 pl-2 border-l border-slate-200 text-[11px] text-slate-400 font-mono">
            <kbd className="px-1.5 py-0.5 bg-white border border-slate-300 rounded shadow-2xs">ESC</kbd>
            <span>to close</span>
          </div>
        </div>

        {/* Suggestion Lists */}
        <div className="max-h-[60vh] overflow-y-auto p-4 space-y-4">
          {/* Recent Queries */}
          {recentQueries.length > 0 && !searchTerm && (
            <div>
              <div className="text-[11px] font-bold text-slate-400 uppercase tracking-wider mb-2 flex items-center">
                <Clock className="w-3 h-3 mr-1 text-slate-400" />
                Recent Queries
              </div>
              <div className="space-y-1">
                {recentQueries.map((q, idx) => (
                  <div
                    key={idx}
                    onClick={() => handleExecute(q)}
                    className="flex items-center justify-between p-2 rounded-lg hover:bg-fiori-blue-light/30 cursor-pointer text-xs text-slate-700 transition-colors group"
                  >
                    <span className="truncate mr-2 font-medium">{q}</span>
                    <CornerDownLeft className="w-3 h-3 text-slate-400 group-hover:text-fiori-blue shrink-0" />
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Categorized Suggestions */}
          {filteredCategories.map((cat) => (
            <div key={cat.category}>
              <div className="text-[11px] font-bold text-slate-400 uppercase tracking-wider mb-2 flex items-center">
                <Sparkles className="w-3 h-3 mr-1 text-fiori-blue" />
                {cat.category}
              </div>
              <div className="space-y-1">
                {cat.prompts.map((p, idx) => (
                  <div
                    key={idx}
                    onClick={() => handleExecute(p)}
                    className="flex items-center justify-between p-2 rounded-lg hover:bg-fiori-blue-light/30 cursor-pointer text-xs text-slate-700 transition-colors group"
                  >
                    <span className="truncate mr-2 font-medium">{p}</span>
                    <ArrowRight className="w-3.5 h-3.5 text-slate-400 group-hover:text-fiori-blue shrink-0 transition-transform group-hover:translate-x-0.5" />
                  </div>
                ))}
              </div>
            </div>
          ))}

          {filteredCategories.length === 0 && (
            <div className="text-center py-8 text-xs text-slate-400">
              Press Enter to ask AI: <span className="font-semibold text-slate-700 font-mono">"{searchTerm}"</span>
            </div>
          )}
        </div>

        {/* Modal Footer */}
        <div className="px-4 py-2 bg-slate-50 border-t border-slate-200 flex items-center justify-between text-[11px] text-slate-500">
          <span>SAP Joule NLP Engine &bull; Fail-Closed AST Guardrails</span>
          <span>Press Enter ↵ to run</span>
        </div>
      </div>
    </div>
  );
};
