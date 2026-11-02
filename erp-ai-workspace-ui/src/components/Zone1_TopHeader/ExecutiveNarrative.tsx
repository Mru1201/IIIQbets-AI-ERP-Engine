import React from 'react';
import { Sparkles, ArrowRight } from 'lucide-react';

interface ExecutiveNarrativeProps {
  thoughtProcess?: string;
  onOpenJoule?: () => void;
}

export const ExecutiveNarrative: React.FC<ExecutiveNarrativeProps> = ({
  thoughtProcess,
  onOpenJoule,
}) => {
  if (!thoughtProcess) return null;

  // Extract first 1 or 2 sentences as high-level summary
  const sentences = thoughtProcess.split(/(?<=[.!?])\s+/);
  const narrative = sentences.slice(0, 2).join(' ');

  return (
    <div className="bg-gradient-to-r from-blue-50/90 via-indigo-50/70 to-white border border-blue-200/80 rounded-lg p-3 mb-4 shadow-2xs">
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-start gap-2.5">
          <div className="p-1 rounded bg-fiori-blue text-white mt-0.5 shrink-0 shadow-2xs">
            <Sparkles className="w-3.5 h-3.5" />
          </div>
          <div>
            <span className="text-xs font-bold text-fiori-blue uppercase tracking-wider block mb-0.5">
              AI Executive Narrative
            </span>
            <p className="text-xs text-slate-700 leading-relaxed font-medium">
              {narrative}
            </p>
          </div>
        </div>

        {onOpenJoule && (
          <button
            onClick={onOpenJoule}
            className="text-xs font-semibold text-fiori-blue hover:text-fiori-blue-hover inline-flex items-center whitespace-nowrap shrink-0 hover:underline pt-1"
          >
            Inspect Reasoning <ArrowRight className="w-3 h-3 ml-1" />
          </button>
        )}
      </div>
    </div>
  );
};
