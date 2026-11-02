import React from 'react';
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { FormulaInspector } from '../components/Zone3_JouleCopilot/FormulaInspector';

describe('FormulaInspector', () => {
  it('renders NetSuite fx formula popover with canonical expression and metastore references', () => {
    const handleClose = vi.fn();
    render(<FormulaInspector metricKey="gross_profit" onClose={handleClose} />);

    expect(screen.getByText('Formula Inspector: Gross Profit')).toBeInTheDocument();
    expect(screen.getByText('Key: gross_profit')).toBeInTheDocument();
    expect(
      screen.getByText('Gross profit = total_sales_revenue - cost_of_goods_sold.')
    ).toBeInTheDocument();

    // Canonical SQL
    expect(screen.getByText('total_sales_revenue - cost_of_goods_sold')).toBeInTheDocument();

    // Required tables
    expect(screen.getByText('voucher')).toBeInTheDocument();
    expect(screen.getByText('voucherdetails')).toBeInTheDocument();
    expect(screen.getByText('products')).toBeInTheDocument();

    // Source reference
    expect(screen.getByText('ledger-backend/routes/profitLossRoutes.js#L177-L178')).toBeInTheDocument();
  });

  it('triggers onClose when close button is clicked', () => {
    const handleClose = vi.fn();
    render(<FormulaInspector metricKey="net_profit" onClose={handleClose} />);

    const closeBtn = screen.getByRole('button', { name: 'Close' });
    fireEvent.click(closeBtn);
    expect(handleClose).toHaveBeenCalled();
  });

  it('copies canonical SQL when Copy Expression button is clicked', async () => {
    const writeTextSpy = vi.fn().mockResolvedValue(undefined);
    Object.assign(navigator, {
      clipboard: {
        writeText: writeTextSpy,
      },
    });

    render(<FormulaInspector metricKey="total_sales_revenue" onClose={() => {}} />);

    const copyBtn = screen.getByRole('button', { name: /Copy Expression/i });
    fireEvent.click(copyBtn);

    expect(writeTextSpy).toHaveBeenCalled();
  });

  it('renders nothing when metricKey is null', () => {
    const { container } = render(<FormulaInspector metricKey={null} onClose={() => {}} />);
    expect(container.firstChild).toBeNull();
  });
});
