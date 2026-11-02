import React, { useState } from 'react';
import { Brain, ChevronDown, ChevronRight, CheckCircle2 } from 'lucide-react';

interface ReasoningTraceProps {
  thoughtProcess?: string;
  tablesUsed?: string[];
  metricsApplied?: string[];
}

export const ReasoningTrace: React.FC<ReasoningTraceProps> = ({
  thoughtProcess,
  tablesUsed = [],
  metricsApplied = [],
}) => {
  const [isExpanded, setIsExpanded] = useState(true);

  if (!thoughtProcess) return null;

  // Split into readable points or sentences
  const steps = thoughtProcess
    .split(/(?<=[.!?])\s+/)
    .filter((s) => s.trim().length > 0);

  return (
    <div className="bg-slate-50 rounded-lg border border-slate-200 overflow-hidden mb-3">
      <button
        onClick={() => setIsExpanded(!isExpanded)}
        className="w-full flex items-center justify-between p-3 text-left font-semibold text-xs text-slate-800 bg-slate-100/70 hover:bg-slate-100 transition-colors"
      >
        <span className="flex items-center">
          <Brain className="w-3.5 h-3.5 mr-1.5 text-fiori-blue" />
          Reasoning & Schema Plan
        </span>
        {isExpanded ? (
          <ChevronDown className="w-3.5 h-3.5 text-slate-500" />
        ) : (
          <ChevronRight className="w-3.5 h-3.5 text-slate-500" />
        )}
      </button>

      {isExpanded && (
        <div className="p-3 text-xs space-y-2 border-t border-slate-200 bg-white">
          <div className="space-y-1.5">
            {steps.map((step, idx) => (
              <div key={idx} className="flex items-start space-x-2 text-slate-700 leading-snug">
                <CheckCircle2 className="w-3.5 h-3.5 text-emerald-600 mt-0.5 shrink-0" />
                <span>{step}</span>
              </div>
            ))}
          </div>

          {(tablesUsed.length > 0 || metricsApplied.length > 0) && (
            <div className="pt-2 mt-2 border-t border-slate-100 flex flex-wrap gap-2 text-[11px]">
              {tablesUsed.length > 0 && (
                <div className="flex items-center gap-1">
                  <span className="text-slate-400 font-medium">Tables:</span>
                  {tablesUsed.map((t) => (
                    <span
                      key={t}
                      className="px-1.5 py-0.5 bg-slate-100 text-slate-600 rounded font-mono"
                    >
                      {t}
                    </span>
                  ))}
                </div>
              )}
              {metricsApplied.length > 0 && (
                <div className="flex items-center gap-1">
                  <span className="text-slate-400 font-medium">Metrics:</span>
                  {metricsApplied.map((m) => (
                    <span
                      key={m}
                      className="px-1.5 py-0.5 bg-blue-50 text-fiori-blue rounded font-mono font-medium"
                    >
                      {m}
                    </span>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
};
