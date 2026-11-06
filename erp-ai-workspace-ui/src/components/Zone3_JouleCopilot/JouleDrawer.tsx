import React from 'react';
import { Bot, ChevronRight, Sparkles, X } from 'lucide-react';
import { ExecutionMetadata } from '../../types/api';
import { ReasoningTrace } from './ReasoningTrace';
import { SqlPreview } from './SqlPreview';
import { RetryTimeline } from './RetryTimeline';
import { MetadataInspector } from './MetadataInspector';
import { MultiTurnInput } from './MultiTurnInput';

interface JouleDrawerProps {
  isOpen: boolean;
  onToggle: () => void;
  metadata?: ExecutionMetadata;
  rowCount?: number;
  onSubmitQuery: (prompt: string) => void;
  isLoading?: boolean;
}

export const JouleDrawer: React.FC<JouleDrawerProps> = ({
  isOpen,
  onToggle,
  metadata,
  rowCount = 0,
  onSubmitQuery,
  isLoading = false,
}) => {
  if (!isOpen) {
    return (
      <button
        onClick={onToggle}
        className="fixed right-0 top-1/2 -translate-y-1/2 z-40 bg-fiori-blue text-white p-2.5 rounded-l-lg shadow-xl hover:bg-fiori-blue-hover transition-all flex items-center space-x-1.5"
        title="Open Reporting Copilot Drawer"
      >
        <Bot className="w-4 h-4" />
        <span className="text-xs font-semibold [writing-mode:vertical-rl] rotate-180 py-1">
          Reporting Copilot
        </span>
      </button>
    );
  }

  return (
    <aside
      className="w-[380px] shrink-0 bg-white border-l border-fiori-border flex flex-col h-[calc(100vh-64px)] sticky top-16 shadow-lg transition-all z-30"
      aria-label="Reporting Copilot Side Drawer"
    >
      {/* Drawer Header */}
      <div className="p-3.5 border-b border-fiori-border flex items-center justify-between bg-gradient-to-r from-blue-50/50 to-white">
        <div className="flex items-center space-x-2">
          <div className="p-1.5 rounded-lg bg-fiori-blue text-white shadow-2xs">
            <Sparkles className="w-4 h-4" />
          </div>
          <div>
            <h2 className="text-xs font-bold text-fiori-dark flex items-center">
              Reporting Copilot
            </h2>
            <span className="text-[10px] text-slate-500 font-medium">
              AI-powered financial intelligence
            </span>
          </div>
        </div>

        <button
          onClick={onToggle}
          className="p-1 rounded text-slate-400 hover:text-slate-600 hover:bg-slate-100 transition-colors"
          title="Collapse Copilot Drawer"
        >
          <ChevronRight className="w-4 h-4" />
        </button>
      </div>

      {/* Drawer Scrollable Content */}
      <div className="flex-1 overflow-y-auto p-3.5 space-y-3">
        {metadata ? (
          <>
            <ReasoningTrace
              thoughtProcess={metadata.thought_process}
              tablesUsed={metadata.tables_used}
              metricsApplied={metadata.metrics_applied}
            />

            <SqlPreview sqlQuery={metadata.verified_sql} />

            <RetryTimeline
              retriesUsed={metadata.retries_used}
              retryHistory={metadata.retry_history}
              latencyMs={metadata.latency_ms}
            />

            <MetadataInspector metadata={metadata} rowCount={rowCount} />
          </>
        ) : (
          <div className="text-center py-16 text-slate-400 text-xs">
            <Bot className="w-8 h-8 mx-auto text-slate-300 mb-2" />
            <p>Execute an AI report query to view step-by-step reasoning and AST verification.</p>
          </div>
        )}
      </div>

      {/* Drawer Footer Input */}
      <div className="p-3.5 border-t border-fiori-border bg-slate-50/50">
        <MultiTurnInput onSubmitQuery={onSubmitQuery} isLoading={isLoading} />
      </div>
    </aside>
  );
};
