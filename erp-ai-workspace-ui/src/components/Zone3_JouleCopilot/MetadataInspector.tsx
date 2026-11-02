import React from 'react';
import { Activity, Clock, Layers, BarChart2 } from 'lucide-react';
import { ExecutionMetadata } from '../../types/api';

interface MetadataInspectorProps {
  metadata?: ExecutionMetadata;
  rowCount?: number;
}

export const MetadataInspector: React.FC<MetadataInspectorProps> = ({
  metadata,
  rowCount = 0,
}) => {
  if (!metadata) return null;

  return (
    <div className="bg-slate-50 rounded-lg border border-slate-200 p-3 mb-3 text-xs">
      <div className="flex items-center font-semibold text-slate-800 mb-2.5">
        <Activity className="w-3.5 h-3.5 mr-1.5 text-fiori-blue" />
        Execution Metadata & Performance
      </div>

      <div className="grid grid-cols-2 gap-2 text-[11px]">
        <div className="bg-white p-2 rounded border border-slate-200/80">
          <span className="text-slate-400 block mb-0.5 flex items-center">
            <Clock className="w-3 h-3 mr-1 text-slate-400" /> Total Latency:
          </span>
          <span className="font-mono font-bold text-slate-800 text-xs">
            {metadata.latency_ms} ms
          </span>
          {metadata.llm_latency_ms && (
            <div className="text-[10px] text-slate-400 font-mono mt-0.5">
              LLM: {metadata.llm_latency_ms}ms | DB: {metadata.db_latency_ms || 0}ms
            </div>
          )}
        </div>

        <div className="bg-white p-2 rounded border border-slate-200/80">
          <span className="text-slate-400 block mb-0.5 flex items-center">
            <BarChart2 className="w-3 h-3 mr-1 text-slate-400" /> Result Size:
          </span>
          <span className="font-mono font-bold text-slate-800 text-xs">
            {rowCount} records
          </span>
          <div className="text-[10px] text-slate-400 font-mono mt-0.5">
            Retries: {metadata.retries_used}
          </div>
        </div>
      </div>

      <div className="mt-2 pt-2 border-t border-slate-200/70 text-[11px] text-slate-600 flex items-center justify-between">
        <span className="flex items-center">
          <Layers className="w-3 h-3 mr-1 text-slate-400" />
          Tables: {metadata.tables_used.join(', ') || 'N/A'}
        </span>
        <span className="font-mono text-emerald-600 font-semibold">200 OK</span>
      </div>
    </div>
  );
};
