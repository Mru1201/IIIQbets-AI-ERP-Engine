import React from 'react';
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { HighDensityTable } from '../components/Zone2_CenterCanvas/HighDensityTable';
import { ColumnDef } from '../types/api';

describe('HighDensityTable', () => {
  const columns: ColumnDef[] = [
    { key: 'customer', label: 'Customer Name', type: 'string', sortable: true },
    { key: 'city', label: 'City', type: 'string', sortable: true },
    { key: 'balance', label: 'Unpaid Balance', type: 'currency', sortable: true, metric_key: 'party_unpaid_balance' },
  ];

  const data = [
    { customer: 'Balaji Traders', city: 'Indore', balance: 55000.0 },
    { customer: 'Kisan Kendra', city: 'Ujjain', balance: 38000.0 },
    { customer: 'Mahalaxmi Agro', city: 'Bhopal', balance: 24500.0 },
  ];

  it('renders enterprise 32px compact grid with correct headers and rows', () => {
    render(<HighDensityTable data={data} columns={columns} />);

    expect(screen.getByText('Customer Name')).toBeInTheDocument();
    expect(screen.getByText('City')).toBeInTheDocument();
    expect(screen.getByText('Unpaid Balance')).toBeInTheDocument();

    expect(screen.getByText('Balaji Traders')).toBeInTheDocument();
    expect(screen.getByText('₹ 55,000.00')).toBeInTheDocument();
    expect(screen.getByText('Kisan Kendra')).toBeInTheDocument();
    expect(screen.getByText('₹ 38,000.00')).toBeInTheDocument();
  });

  it('filters rows when user types into the global search input', () => {
    render(<HighDensityTable data={data} columns={columns} />);

    const searchInput = screen.getByPlaceholderText('Filter records...');
    fireEvent.change(searchInput, { target: { value: 'Bhopal' } });

    expect(screen.getByText('Mahalaxmi Agro')).toBeInTheDocument();
    expect(screen.queryByText('Balaji Traders')).not.toBeInTheDocument();
    expect(screen.queryByText('Kisan Kendra')).not.toBeInTheDocument();
  });

  it('toggles column visibility via ColumnToggleMenu', () => {
    render(<HighDensityTable data={data} columns={columns} />);

    const columnsBtn = screen.getByTitle('Toggle visible columns');
    fireEvent.click(columnsBtn);

    // City checkbox
    const cityCheckbox = screen.getByLabelText('City');
    expect(cityCheckbox).toBeChecked();

    // Toggle off City column
    fireEvent.click(cityCheckbox);
    expect(cityCheckbox).not.toBeChecked();

    // City header should no longer be in the document
    expect(screen.queryByText('Indore')).not.toBeInTheDocument();
  });

  it('triggers onOpenFormula when clicking fx header badge', () => {
    const handleOpenFormula = vi.fn();
    render(<HighDensityTable data={data} columns={columns} onOpenFormula={handleOpenFormula} />);

    const fxButton = screen.getByTitle('Inspect NetSuite fx Formula');
    fireEvent.click(fxButton);

    expect(handleOpenFormula).toHaveBeenCalledWith('party_unpaid_balance');
  });
});
