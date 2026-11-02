import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { AnalyticalListPage } from '../components/AnalyticalListPage';
import { generateMockReport } from '../api/client';

// Mock ResizeObserver for jsdom
global.ResizeObserver = class {
  observe() {}
  unobserve() {}
  disconnect() {}
};

describe('AnalyticalListPage (SAP Fiori 3-Zone ALP Integration)', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    // Mock global fetch so API calls resolve immediately with valid response
    global.fetch = vi.fn().mockImplementation(async (url: any, options: any) => {
      const body = options?.body ? JSON.parse(options.body) : {};
      return {
        ok: true,
        json: async () => generateMockReport({ user_query: body.user_query || 'Show net profit' }),
      } as any;
    });
  });

  it('renders 3-Zone layout with Zone 1 KPIs, Zone 2 Table, and Zone 3 Joule Copilot drawer', async () => {
    render(
      <AnalyticalListPage
        apiUrl="http://localhost:8000"
        tenantId="shree_shashwatraj_agro"
        defaultQuery="Show net profit and gross profit for 2026"
      />
    );

    // Header brand and tenant
    expect(screen.getByText('Autonomous AI ERP Reporting Workspace')).toBeInTheDocument();
    expect(screen.getByText('shree_shashwatraj_agro')).toBeInTheDocument();

    // Zone 1: Executive Narrative and Date presets
    await waitFor(() => {
      expect(screen.getByText('AI Executive Narrative')).toBeInTheDocument();
    });

    expect(screen.getByText('Total Revenue')).toBeInTheDocument();
    expect(screen.getAllByText('Gross Profit').length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText(/Net Profit/).length).toBeGreaterThanOrEqual(1);

    // Zone 2: High Density Table
    expect(screen.getByText('Financial Metric')).toBeInTheDocument();
    expect(screen.getByText('Gross Sales Revenue')).toBeInTheDocument();

    // Zone 3: Joule Drawer
    expect(screen.getByText('Joule Enterprise Copilot')).toBeInTheDocument();
    expect(screen.getByText('Reasoning & Schema Plan')).toBeInTheDocument();
    expect(screen.getByText('Executable SQL')).toBeInTheDocument();
    expect(screen.getByText('Self-Correction Timeline')).toBeInTheDocument();
  });

  it('opens and closes Cmd+K Awesomebar Modal when trigger is clicked', async () => {
    render(
      <AnalyticalListPage
        apiUrl="http://localhost:8000"
        tenantId="shree_shashwatraj_agro"
      />
    );

    // Click Cmd+K button
    const awesomebarBtn = screen.getByText('⌘K');
    fireEvent.click(awesomebarBtn);

    expect(
      screen.getByPlaceholderText("Ask anything... (e.g. 'Show gross profit by retailer in Q3 2026')")
    ).toBeInTheDocument();

    // Close with ESC
    fireEvent.keyDown(window, { key: 'Escape' });
    await waitFor(() => {
      expect(
        screen.queryByPlaceholderText("Ask anything... (e.g. 'Show gross profit by retailer in Q3 2026')")
      ).not.toBeInTheDocument();
    });
  });

  it('toggles Joule Copilot drawer collapse', async () => {
    render(
      <AnalyticalListPage
        apiUrl="http://localhost:8000"
        tenantId="shree_shashwatraj_agro"
      />
    );

    const toggleBtn = screen.getByTitle('Collapse Copilot Drawer');
    fireEvent.click(toggleBtn);

    // Drawer is collapsed, floating button appears
    expect(screen.getByTitle('Open Joule Copilot Drawer')).toBeInTheDocument();

    // Re-open drawer
    fireEvent.click(screen.getByTitle('Open Joule Copilot Drawer'));
    expect(screen.getByText('Joule Enterprise Copilot')).toBeInTheDocument();
  });

  it('opens and closes Awesomebar Modal via global Cmd+K and Ctrl+K keyboard shortcuts', async () => {
    render(
      <AnalyticalListPage
        apiUrl="http://localhost:8000"
        tenantId="shree_shashwatraj_agro"
      />
    );

    // Press Cmd+K to open
    fireEvent.keyDown(window, { key: 'k', metaKey: true });
    expect(
      screen.getByPlaceholderText("Ask anything... (e.g. 'Show gross profit by retailer in Q3 2026')")
    ).toBeInTheDocument();

    // Press Cmd+K again to toggle close
    fireEvent.keyDown(window, { key: 'k', metaKey: true });
    await waitFor(() => {
      expect(
        screen.queryByPlaceholderText("Ask anything... (e.g. 'Show gross profit by retailer in Q3 2026')")
      ).not.toBeInTheDocument();
    });

    // Press Ctrl+K to open
    fireEvent.keyDown(window, { key: 'k', ctrlKey: true });
    expect(
      screen.getByPlaceholderText("Ask anything... (e.g. 'Show gross profit by retailer in Q3 2026')")
    ).toBeInTheDocument();
  });

  it('invokes callback props onReportGenerated and onFilterChanged when report data arrives', async () => {
    const reportSpy = vi.fn();
    const filterSpy = vi.fn();

    render(
      <AnalyticalListPage
        apiUrl="http://localhost:8000"
        tenantId="shree_shashwatraj_agro"
        onReportGenerated={reportSpy}
        onFilterChanged={filterSpy}
      />
    );

    await waitFor(() => {
      expect(reportSpy).toHaveBeenCalled();
      expect(filterSpy).toHaveBeenCalled();
    });

    expect(reportSpy.mock.calls[0][0].status).toBe('success');
    expect(reportSpy.mock.calls[0][0].ui_schema).toBeDefined();
  });
});
