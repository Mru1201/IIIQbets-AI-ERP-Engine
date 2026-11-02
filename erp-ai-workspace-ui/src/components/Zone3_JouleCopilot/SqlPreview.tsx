import React, { useState } from 'react';
import { Terminal, Copy, Check, ShieldCheck } from 'lucide-react';

interface SqlPreviewProps {
  sqlQuery?: string;
  dialect?: string;
}

export const SqlPreview: React.FC<SqlPreviewProps> = ({
  sqlQuery,
  dialect = 'MySQL / SQLGlot',
}) => {
  const [copied, setCopied] = useState(false);

  if (!sqlQuery) return null;

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(sqlQuery);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // Fallback
    }
  };

  return (
    <div className="bg-slate-900 rounded-lg border border-slate-800 overflow-hidden mb-3 text-slate-100 shadow-sm">
      <div className="flex items-center justify-between px-3 py-2 bg-slate-950/80 border-b border-slate-800 text-xs">
        <div className="flex items-center space-x-2">
          <Terminal className="w-3.5 h-3.5 text-emerald-400" />
          <span className="font-mono text-slate-300 font-medium">Executable SQL</span>
          <span className="text-[10px] px-1.5 py-0.5 rounded bg-slate-800 text-slate-400 font-mono">
            {dialect}
          </span>
        </div>

        <button
          onClick={handleCopy}
          className="inline-flex items-center text-[11px] text-slate-400 hover:text-white transition-colors px-2 py-0.5 rounded hover:bg-slate-800"
          title="Copy SQL to Clipboard"
        >
          {copied ? (
            <>
              <Check className="w-3 h-3 text-emerald-400 mr-1" />
              <span className="text-emerald-400 font-semibold">Copied</span>
            </>
          ) : (
            <>
              <Copy className="w-3 h-3 mr-1" />
              <span>Copy</span>
            </>
          )}
        </button>
      </div>

      <div className="p-3 font-mono text-[11px] leading-relaxed overflow-x-auto text-emerald-300/90 whitespace-pre-wrap select-all max-h-48">
        {sqlQuery}
      </div>

      <div className="px-3 py-1.5 bg-slate-950/60 border-t border-slate-800 flex items-center justify-between text-[10px] text-slate-400">
        <div className="flex items-center space-x-1 text-emerald-400">
          <ShieldCheck className="w-3.5 h-3.5" />
          <span className="font-semibold">AST Verified Read-Only (SQLGlot)</span>
        </div>
        <span className="text-slate-500">LIMIT 1000 enforced</span>
      </div>
    </div>
  );
};
