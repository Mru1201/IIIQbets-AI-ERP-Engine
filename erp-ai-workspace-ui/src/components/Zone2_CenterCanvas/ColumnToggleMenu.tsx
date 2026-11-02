import React, { useState, useRef, useEffect } from 'react';
import { Columns, Check } from 'lucide-react';
import { Table } from '@tanstack/react-table';

interface ColumnToggleMenuProps {
  table: Table<any>;
}

export const ColumnToggleMenu: React.FC<ColumnToggleMenuProps> = ({ table }) => {
  const [isOpen, setIsOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        setIsOpen(false);
      }
    };
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  const allColumns = table.getAllLeafColumns();

  return (
    <div className="relative inline-block text-left" ref={menuRef}>
      <button
        onClick={() => setIsOpen(!isOpen)}
        className="inline-flex items-center text-xs font-medium px-2.5 py-1.5 bg-white border border-slate-300 text-slate-700 hover:bg-slate-50 rounded shadow-2xs transition-colors"
        title="Toggle visible columns"
      >
        <Columns className="w-3.5 h-3.5 mr-1.5 text-slate-500" />
        Columns
      </button>

      {isOpen && (
        <div className="origin-top-right absolute right-0 mt-1 w-48 rounded-md shadow-lg bg-white ring-1 ring-black ring-opacity-5 z-50 py-1 divide-y divide-slate-100 max-h-64 overflow-y-auto">
          <div className="px-3 py-1.5 text-[11px] font-bold text-slate-400 uppercase tracking-wider">
            Toggle Columns
          </div>
          <div className="py-1">
            {allColumns.map((col) => {
              const isVisible = col.getIsVisible();
              const headerText =
                typeof col.columnDef.header === 'string'
                  ? col.columnDef.header
                  : col.id;

              return (
                <label
                  key={col.id}
                  className="flex items-center px-3 py-1 text-xs text-slate-700 hover:bg-slate-50 cursor-pointer select-none"
                >
                  <input
                    type="checkbox"
                    checked={isVisible}
                    onChange={col.getToggleVisibilityHandler()}
                    className="h-3.5 w-3.5 text-fiori-blue rounded border-slate-300 mr-2 focus:ring-0"
                  />
                  <span className="truncate">{headerText}</span>
                  {isVisible && <Check className="w-3 h-3 text-fiori-blue ml-auto shrink-0" />}
                </label>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
};
