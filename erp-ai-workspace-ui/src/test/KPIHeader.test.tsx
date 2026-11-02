import React from 'react';
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { KPIHeader } from '../components/Zone1_TopHeader/KPIHeader';
import { KPICard } from '../types/api';

describe('KPIHeader', () => {
  const kpis: KPICard[] = [
    {
      label: 'Total Revenue',
      value: '10,368.00 INR',
      trend: 'positive',
      subtext: 'Gross Sales Vouchers',
      metric_key: 'total_sales_revenue',
    },
    {
      label: 'Net Loss',
      value: '-43,232.00 INR',
      trend: 'negative',
      subtext: 'Operating Margin',
      metric_key: 'net_profit',
    },
    {
      label: 'Debtor Count',
      value: 12,
      trend: 'neutral',
      subtext: 'Active accounts',
    },
  ];

  it('renders all KPI cards with labels, formatted values, and subtexts', () => {
    render(<KPIHeader kpis={kpis} />);

    expect(screen.getByText('Total Revenue')).toBeInTheDocument();
    expect(screen.getByText('₹ 10,368.00')).toBeInTheDocument();
    expect(screen.getByText('Gross Sales Vouchers')).toBeInTheDocument();

    expect(screen.getByText('Net Loss')).toBeInTheDocument();
    expect(screen.getByText('-₹ 43,232.00')).toBeInTheDocument();

    expect(screen.getByText('Debtor Count')).toBeInTheDocument();
    expect(screen.getByText('12')).toBeInTheDocument();
  });

  it('renders directional sentiment trend badges (positive, negative, neutral)', () => {
    render(<KPIHeader kpis={kpis} />);

    expect(screen.getByText('Up')).toBeInTheDocument();
    expect(screen.getByText('Down')).toBeInTheDocument();
    expect(screen.getByText('Neutral')).toBeInTheDocument();
  });

  it('calls onSelectKpi when card is clicked', () => {
    const handleSelect = vi.fn();
    render(<KPIHeader kpis={kpis} onSelectKpi={handleSelect} />);

    fireEvent.click(screen.getByText('Total Revenue'));
    expect(handleSelect).toHaveBeenCalledWith(kpis[0]);
  });

  it('renders NetSuite fx formula button and triggers onOpenFormula', () => {
    const handleOpenFormula = vi.fn();
    render(<KPIHeader kpis={kpis} onOpenFormula={handleOpenFormula} />);

    const fxButtons = screen.getAllByTitle('Inspect NetSuite fx Formula');
    expect(fxButtons.length).toBe(2);

    fireEvent.click(fxButtons[0]);
    expect(handleOpenFormula).toHaveBeenCalledWith('total_sales_revenue');
  });
});
