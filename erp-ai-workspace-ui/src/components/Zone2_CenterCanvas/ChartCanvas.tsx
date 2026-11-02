import React, { useState } from 'react';
import {
  ResponsiveContainer,
  BarChart,
  Bar,
  LineChart,
  Line,
  PieChart,
  Pie,
  Cell,
  XAxis,
  YAxis,
  Tooltip,
  Legend,
  CartesianGrid,
} from 'recharts';
import { BarChart3, LineChart as LineIcon, PieChart as PieIcon } from 'lucide-react';
import { ColumnDef, VisualizationType } from '../../types/api';
import { formatCurrency, formatNumber } from '../../utils/formatters';

interface ChartCanvasProps {
  data: Record<string, any>[];
  columns: ColumnDef[];
  suggestedVisualization: VisualizationType;
  onCrossFilter?: (field: string, value: any) => void;
  activeFilterValue?: any;
}

const FIORI_COLORS = [
  '#0070F2', // Fiori Blue
  '#107E3E', // Positive Green
  '#E9730C', // Amber Orange
  '#6366F1', // Indigo
  '#06B6D4', // Cyan
  '#EC4899', // Pink
  '#8B5CF6', // Purple
  '#F59E0B', // Yellow
];

export const ChartCanvas: React.FC<ChartCanvasProps> = ({
  data,
  columns,
  suggestedVisualization,
  onCrossFilter,
  activeFilterValue,
}) => {
  // Map visualization type to one of the 3 primary chart formats
  const initialType = () => {
    if (suggestedVisualization === 'line_chart') return 'line';
    if (suggestedVisualization === 'donut_chart') return 'donut';
    return 'bar';
  };

  const [chartType, setChartType] = useState<'bar' | 'line' | 'donut'>(initialType);

  if (!data || data.length === 0) return null;

  // Identify category dimension (string or date) and numeric metric columns
  const categoryCol =
    columns.find((c) => c.type === 'string' || c.type === 'date') || columns[0];
  const numericCols = columns.filter((c) => c.type === 'currency' || c.type === 'number');

  if (!categoryCol || numericCols.length === 0) return null;

  const primaryMetric = numericCols[0];
  const catKey = categoryCol.key;

  // Custom Recharts Tooltip
  const CustomTooltip = ({ active, payload, label }: any) => {
    if (active && payload && payload.length) {
      return (
        <div className="bg-slate-900/95 backdrop-blur-md text-white p-2.5 rounded shadow-lg text-xs border border-slate-700 min-w-36">
          <p className="font-semibold text-slate-200 border-b border-slate-700 pb-1 mb-1.5">
            {label || payload[0]?.name}
          </p>
          {payload.map((entry: any, i: number) => {
            const isCurr = primaryMetric.type === 'currency';
            const formatted = isCurr ? formatCurrency(entry.value) : formatNumber(entry.value);
            return (
              <div key={i} className="flex justify-between items-center py-0.5 space-x-3">
                <span className="flex items-center text-slate-300">
                  <span
                    className="w-2 h-2 rounded-full mr-1.5 shrink-0"
                    style={{ backgroundColor: entry.color }}
                  />
                  {entry.name}:
                </span>
                <span className="font-mono font-bold text-white">{formatted}</span>
              </div>
            );
          })}
        </div>
      );
    }
    return null;
  };

  return (
    <div className="bg-white rounded-lg p-4 border border-fiori-border shadow-xs mb-4">
      <div className="flex items-center justify-between mb-3 border-b border-slate-100 pb-2">
        <div className="flex items-center space-x-2">
          <span className="text-xs font-bold text-fiori-dark uppercase tracking-wider">
            Visual Analysis:
          </span>
          <span className="text-xs text-slate-500 font-medium">
            {categoryCol.label} vs {numericCols.map((c) => c.label).join(', ')}
          </span>
        </div>

        {/* Visual Format Switcher */}
        <div className="inline-flex items-center rounded-md bg-slate-100 p-0.5 border border-slate-200 text-xs">
          <button
            onClick={() => setChartType('bar')}
            className={`flex items-center px-2 py-1 rounded transition-colors ${
              chartType === 'bar' ? 'bg-white font-semibold text-fiori-blue shadow-2xs' : 'text-slate-600 hover:text-slate-900'
            }`}
            title="Bar Chart"
          >
            <BarChart3 className="w-3.5 h-3.5 mr-1" />
            Bar
          </button>
          <button
            onClick={() => setChartType('line')}
            className={`flex items-center px-2 py-1 rounded transition-colors ${
              chartType === 'line' ? 'bg-white font-semibold text-fiori-blue shadow-2xs' : 'text-slate-600 hover:text-slate-900'
            }`}
            title="Line Chart"
          >
            <LineIcon className="w-3.5 h-3.5 mr-1" />
            Line
          </button>
          <button
            onClick={() => setChartType('donut')}
            className={`flex items-center px-2 py-1 rounded transition-colors ${
              chartType === 'donut' ? 'bg-white font-semibold text-fiori-blue shadow-2xs' : 'text-slate-600 hover:text-slate-900'
            }`}
            title="Donut / Pie Chart"
          >
            <PieIcon className="w-3.5 h-3.5 mr-1" />
            Donut
          </button>
        </div>
      </div>

      <div className="h-64 w-full">
        <ResponsiveContainer width="100%" height="100%">
          {chartType === 'bar' ? (
            <BarChart
              data={data}
              margin={{ top: 10, right: 20, left: 20, bottom: 25 }}
              onClick={(state) => {
                if (state && state.activeLabel !== undefined && onCrossFilter) {
                  onCrossFilter(catKey, state.activeLabel);
                }
              }}
            >
              <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#E2E8F0" />
              <XAxis
                dataKey={catKey}
                tick={{ fontSize: 11, fill: '#64748B' }}
                interval={0}
                angle={-15}
                textAnchor="end"
              />
              <YAxis
                tick={{ fontSize: 11, fill: '#64748B' }}
                tickFormatter={(val) =>
                  primaryMetric.type === 'currency' ? formatCurrency(val) : formatNumber(val)
                }
              />
              <Tooltip content={<CustomTooltip />} />
              <Legend wrapperStyle={{ fontSize: '11px', paddingTop: '8px' }} />
              {numericCols.map((col, idx) => (
                <Bar
                  key={col.key}
                  dataKey={col.key}
                  name={col.label}
                  fill={FIORI_COLORS[idx % FIORI_COLORS.length]}
                  radius={[4, 4, 0, 0]}
                  cursor="pointer"
                >
                  {data.map((entry, index) => (
                    <Cell
                      key={`cell-${index}`}
                      fill={
                        activeFilterValue && entry[catKey] === activeFilterValue
                          ? '#0057B8'
                          : FIORI_COLORS[idx % FIORI_COLORS.length]
                      }
                      opacity={activeFilterValue && entry[catKey] !== activeFilterValue ? 0.4 : 1}
                    />
                  ))}
                </Bar>
              ))}
            </BarChart>
          ) : chartType === 'line' ? (
            <LineChart
              data={data}
              margin={{ top: 10, right: 20, left: 20, bottom: 25 }}
              onClick={(state) => {
                if (state && state.activeLabel !== undefined && onCrossFilter) {
                  onCrossFilter(catKey, state.activeLabel);
                }
              }}
            >
              <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#E2E8F0" />
              <XAxis
                dataKey={catKey}
                tick={{ fontSize: 11, fill: '#64748B' }}
                interval={0}
                angle={-15}
                textAnchor="end"
              />
              <YAxis
                tick={{ fontSize: 11, fill: '#64748B' }}
                tickFormatter={(val) =>
                  primaryMetric.type === 'currency' ? formatCurrency(val) : formatNumber(val)
                }
              />
              <Tooltip content={<CustomTooltip />} />
              <Legend wrapperStyle={{ fontSize: '11px', paddingTop: '8px' }} />
              {numericCols.map((col, idx) => (
                <Line
                  key={col.key}
                  type="monotone"
                  dataKey={col.key}
                  name={col.label}
                  stroke={FIORI_COLORS[idx % FIORI_COLORS.length]}
                  strokeWidth={2.5}
                  dot={{ r: 4, strokeWidth: 1.5, fill: '#FFF' }}
                  activeDot={{ r: 6 }}
                />
              ))}
            </LineChart>
          ) : (
            <PieChart>
              <Tooltip content={<CustomTooltip />} />
              <Legend wrapperStyle={{ fontSize: '11px' }} />
              <Pie
                data={data}
                dataKey={primaryMetric.key}
                nameKey={catKey}
                cx="50%"
                cy="50%"
                innerRadius={50}
                outerRadius={80}
                paddingAngle={2}
                cursor="pointer"
                onClick={(entry) => {
                  if (entry && onCrossFilter) {
                    onCrossFilter(catKey, entry[catKey] || entry.name);
                  }
                }}
              >
                {data.map((entry, index) => (
                  <Cell
                    key={`pie-cell-${index}`}
                    fill={FIORI_COLORS[index % FIORI_COLORS.length]}
                    opacity={activeFilterValue && entry[catKey] !== activeFilterValue ? 0.35 : 1}
                  />
                ))}
              </Pie>
            </PieChart>
          )}
        </ResponsiveContainer>
      </div>

      <div className="mt-2 text-right">
        <span className="text-[11px] text-slate-400 italic">
          💡 Click chart element to cross-filter table rows below
        </span>
      </div>
    </div>
  );
};
