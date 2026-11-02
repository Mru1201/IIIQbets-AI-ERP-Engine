import React from 'react';
import { History, CheckCircle, AlertTriangle, ArrowDown } from 'lucide-react';
import { RetryHistoryItem } from '../../types/api';

interface RetryTimelineProps {
  retriesUsed?: number;
  retryHistory?: RetryHistoryItem[];
  latencyMs?: number;
}

export const RetryTimeline: React.FC<RetryTimelineProps> = ({
  retriesUsed = 0,
  retryHistory = [],
  latencyMs = 0,
}) => {
  return (
    <div className="bg-slate-50 rounded-lg border border-slate-200 p-3 mb-3 text-xs">
      <div className="flex items-center justify-between mb-2">
        <span className="flex items-center font-semibold text-slate-800">
          <History className="w-3.5 h-3.5 mr-1.5 text-fiori-blue" />
          Self-Correction Timeline
        </span>
        <span className="text-[11px] px-1.5 py-0.5 rounded font-mono font-medium bg-slate-200 text-slate-700">
          {retriesUsed === 0 ? 'Single-Shot' : `${retriesUsed} ${retriesUsed === 1 ? 'Retry' : 'Retries'}`}
        </span>
      </div>

      {retriesUsed === 0 && (
        <div className="flex items-center space-x-2 text-emerald-700 bg-emerald-50 border border-emerald-200/60 p-2 rounded">
          <CheckCircle className="w-4 h-4 shrink-0" />
          <div>
            <div className="font-semibold text-xs">Attempt 1: Verified Success</div>
            <div className="text-[11px] text-emerald-600">
              Query passed AST guardrails and executed cleanly in {latencyMs}ms.
            </div>
          </div>
        </div>
      )}

      {retriesUsed > 0 && (
        <div className="space-y-2 mt-2">
          {retryHistory.map((item, idx) => (
            <div key={idx} className="space-y-1.5">
              <div className="flex items-start space-x-2 text-rose-800 bg-rose-50 border border-rose-200/80 p-2 rounded">
                <AlertTriangle className="w-4 h-4 text-rose-600 shrink-0 mt-0.5" />
                <div className="overflow-hidden">
                  <div className="font-semibold text-xs">Attempt {item.attempt}: Execution Failure</div>
                  <div className="text-[11px] font-mono text-rose-700 truncate" title={item.error_message}>
                    {item.error_message}
                  </div>
                </div>
              </div>

              {item.self_corrected_sql && (
                <div className="flex items-center justify-center py-0.5 text-slate-400">
                  <ArrowDown className="w-3.5 h-3.5" />
                </div>
              )}

              {item.self_corrected_sql && (
                <div className="text-[11px] bg-blue-50 border border-blue-200 p-2 rounded text-slate-700">
                  <span className="font-semibold text-fiori-blue">Self-Correction Diff: </span>
                  <span className="font-mono text-slate-600">{item.self_corrected_sql}</span>
                </div>
              )}
            </div>
          ))}

          <div className="flex items-center space-x-2 text-emerald-700 bg-emerald-50 border border-emerald-200 p-2 rounded mt-2">
            <CheckCircle className="w-4 h-4 shrink-0" />
            <div>
              <div className="font-semibold text-xs">Attempt {retriesUsed + 1}: Self-Corrected & Verified</div>
              <div className="text-[11px] text-emerald-600">
                Self-healed SQL executed successfully in {latencyMs}ms.
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};
