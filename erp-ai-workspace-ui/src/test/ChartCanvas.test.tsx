import React from 'react';
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { ChartCanvas } from '../components/Zone2_CenterCanvas/ChartCanvas';
import { ColumnDef } from '../types/api';

// Mock ResizeObserver for jsdom
global.ResizeObserver = class {
  observe() {}
  unobserve() {}
  disconnect() {}
};

describe('ChartCanvas', () => {
  const columns: ColumnDef[] = [
    { key: 'category', label: 'Category', type: 'string' },
    { key: 'sales', label: 'Sales Revenue', type: 'currency' },
  ];

  const data = [
    { category: 'Seeds', sales: 12000.0 },
    { category: 'Fertilizers', sales: 25000.0 },
    { category: 'Pesticides', sales: 18000.0 },
  ];

  it('renders visual analysis header and format switcher buttons', () => {
    render(
      <ChartCanvas
        data={data}
        columns={columns}
        suggestedVisualization="bar_chart"
      />
    );

    expect(screen.getByText('Visual Analysis:')).toBeInTheDocument();
    expect(screen.getByText('Category vs Sales Revenue')).toBeInTheDocument();

    expect(screen.getByTitle('Bar Chart')).toBeInTheDocument();
    expect(screen.getByTitle('Line Chart')).toBeInTheDocument();
    expect(screen.getByTitle('Donut / Pie Chart')).toBeInTheDocument();
  });

  it('switches between Bar, Line, and Donut views when switcher buttons are clicked', () => {
    render(
      <ChartCanvas
        data={data}
        columns={columns}
        suggestedVisualization="bar_chart"
      />
    );

    const lineBtn = screen.getByTitle('Line Chart');
    fireEvent.click(lineBtn);
    expect(lineBtn).toHaveClass('font-semibold');

    const donutBtn = screen.getByTitle('Donut / Pie Chart');
    fireEvent.click(donutBtn);
    expect(donutBtn).toHaveClass('font-semibold');

    const barBtn = screen.getByTitle('Bar Chart');
    fireEvent.click(barBtn);
    expect(barBtn).toHaveClass('font-semibold');
  });

  it('handles empty data gracefully by returning null', () => {
    const { container } = render(
      <ChartCanvas
        data={[]}
        columns={columns}
        suggestedVisualization="bar_chart"
      />
    );
    expect(container.firstChild).toBeNull();
  });
});
