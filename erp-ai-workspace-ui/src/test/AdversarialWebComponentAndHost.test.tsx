import React from 'react';
import { describe, it, expect, vi, beforeAll } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import fs from 'fs';
import path from 'path';
import '../webcomponent/ErpAiReporterElement';
import { FormulaInspector } from '../components/Zone3_JouleCopilot/FormulaInspector';
import { KPIHeader } from '../components/Zone1_TopHeader/KPIHeader';
import { HighDensityTable } from '../components/Zone2_CenterCanvas/HighDensityTable';
import { KPICard, ColumnDef } from '../types/api';

// Polyfill ResizeObserver for recharts ResponsiveContainer in JSDOM
beforeAll(() => {
  global.ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  };
});

describe('Adversarial Web Component <erp-ai-reporter> Lifecycle & Contracts', () => {
  it('attaches open shadow root on initial connection (shadowRoot !== null)', () => {
    const el = document.createElement('erp-ai-reporter') as HTMLElement;
    document.body.appendChild(el);

    expect(el.shadowRoot).not.toBeNull();
    expect(el.shadowRoot?.mode).toBe('open');

    // Style encapsulation injected
    const styleEl = el.shadowRoot?.querySelector('style');
    expect(styleEl).not.toBeNull();

    // Mount point created
    const rootEl = el.shadowRoot?.querySelector('#erp-ai-reporter-root');
    expect(rootEl).not.toBeNull();

    document.body.removeChild(el);
  });

  it('reconnecting <erp-ai-reporter> does not throw NotSupportedError and maintains shadow root', () => {
    const el = document.createElement('erp-ai-reporter') as any;
    document.body.appendChild(el);
    expect(el.shadowRoot).not.toBeNull();

    // Calling connectedCallback when shadowRoot already exists must not throw
    expect(() => {
      el.connectedCallback();
    }).not.toThrow();

    // Disconnecting and reconnecting
    document.body.removeChild(el);
    document.body.appendChild(el);
    expect(el.shadowRoot).not.toBeNull();
    expect(el.shadowRoot?.querySelector('#erp-ai-reporter-root')).not.toBeNull();

    document.body.removeChild(el);
  });

  it('observedAttributes includes dev-mode and theme attributes', () => {
    const CustomElementClass: any = customElements.get('erp-ai-reporter');
    const observed = CustomElementClass.observedAttributes || [];

    expect(observed).toEqual(['api-url', 'tenant-id', 'token', 'default-query', 'dev-mode', 'theme']);
    expect(observed).toContain('dev-mode');
    expect(observed).toContain('theme');
  });

  it('custom element dispatches reportGenerated and filterChanged events', async () => {
    const el = document.createElement('erp-ai-reporter') as HTMLElement;
    const reportSpy = vi.fn();
    const filterSpy = vi.fn();

    el.addEventListener('reportGenerated', reportSpy);
    el.addEventListener('filterChanged', filterSpy);

    document.body.appendChild(el);

    // Allow React tree and initial mock query to mount
    await new Promise((resolve) => setTimeout(resolve, 300));

    expect(reportSpy).toHaveBeenCalled();
    expect(filterSpy).toHaveBeenCalled();

    const reportDetail = reportSpy.mock.calls[0][0].detail;
    expect(reportDetail).toBeDefined();
    expect(reportDetail.ui_schema).toBeDefined();

    document.body.removeChild(el);
  });
});

describe('Adversarial NetSuite fx Formula Inspector for Canonical Metrics', () => {
  it('displays canonical formula, required tables, and source references for gross_profit', () => {
    const handleClose = vi.fn();
    render(<FormulaInspector metricKey="gross_profit" onClose={handleClose} />);

    expect(screen.getByText('Formula Inspector: Gross Profit')).toBeInTheDocument();
    expect(screen.getByText('Key: gross_profit')).toBeInTheDocument();
    expect(screen.getByText('total_sales_revenue - cost_of_goods_sold')).toBeInTheDocument();

    // Required tables: voucher, voucherdetails, products
    expect(screen.getByText('voucher')).toBeInTheDocument();
    expect(screen.getByText('voucherdetails')).toBeInTheDocument();
    expect(screen.getByText('products')).toBeInTheDocument();

    // Metastore reference
    expect(screen.getByText('ledger-backend/routes/profitLossRoutes.js#L177-L178')).toBeInTheDocument();
  });

  it('displays canonical formula, required tables, and source references for net_profit', () => {
    const handleClose = vi.fn();
    render(<FormulaInspector metricKey="net_profit" onClose={handleClose} />);

    expect(screen.getByText('Formula Inspector: Net Profit')).toBeInTheDocument();
    expect(screen.getByText('Key: net_profit')).toBeInTheDocument();
    expect(
      screen.getByText('total_sales_revenue - cost_of_goods_sold - total_expenses')
    ).toBeInTheDocument();

    // Required tables: voucher, voucherdetails, products, expensive
    expect(screen.getByText('voucher')).toBeInTheDocument();
    expect(screen.getByText('voucherdetails')).toBeInTheDocument();
    expect(screen.getByText('products')).toBeInTheDocument();
    expect(screen.getByText('expensive')).toBeInTheDocument();

    // Metastore reference
    expect(screen.getByText('ledger-backend/routes/profitLossRoutes.js#L180-L203')).toBeInTheDocument();
  });

  it('displays canonical formula, required tables, and source references for party_unpaid_balance', () => {
    const handleClose = vi.fn();
    render(<FormulaInspector metricKey="party_unpaid_balance" onClose={handleClose} />);

    expect(screen.getByText('Formula Inspector: Party Unpaid Balance')).toBeInTheDocument();
    expect(screen.getByText('Key: party_unpaid_balance')).toBeInTheDocument();

    // Canonical SQL contains accounts.unpaid_amount cast
    const sqlElement = screen.getByText(/a\.unpaid_amount AS DECIMAL/i);
    expect(sqlElement).toBeInTheDocument();

    // Required tables: accounts
    expect(screen.getByText('accounts')).toBeInTheDocument();

    // Metastore reference
    expect(screen.getByText('ledger-backend/routes/VochurRoutes.js#L1396-L1440')).toBeInTheDocument();
  });

  it('end-to-end: clicking fx badge in KPIHeader opens popover for gross_profit', () => {
    const kpis: KPICard[] = [
      {
        label: 'Gross Profit',
        value: '50,000 INR',
        trend: 'positive',
        metric_key: 'gross_profit',
      },
    ];

    let inspectedKey: string | null = null;
    const { rerender } = render(
      <div>
        <KPIHeader kpis={kpis} onOpenFormula={(key) => (inspectedKey = key)} />
        <FormulaInspector metricKey={inspectedKey} onClose={() => (inspectedKey = null)} />
      </div>
    );

    const fxBtn = screen.getByTitle('Inspect NetSuite fx Formula');
    fireEvent.click(fxBtn);
    expect(inspectedKey).toBe('gross_profit');

    rerender(
      <div>
        <KPIHeader kpis={kpis} onOpenFormula={(key) => (inspectedKey = key)} />
        <FormulaInspector metricKey={inspectedKey} onClose={() => (inspectedKey = null)} />
      </div>
    );

    expect(screen.getByText('Formula Inspector: Gross Profit')).toBeInTheDocument();
    expect(screen.getByText('total_sales_revenue - cost_of_goods_sold')).toBeInTheDocument();
  });

  it('end-to-end: clicking fx badge in HighDensityTable opens popover for party_unpaid_balance', () => {
    const columns: ColumnDef[] = [
      { key: 'customer', label: 'Customer', type: 'string' },
      { key: 'unpaid', label: 'Unpaid Amount', type: 'currency', metric_key: 'party_unpaid_balance' },
    ];
    const data = [{ customer: 'ABC Corp', unpaid: 15000 }];

    let inspectedKey: string | null = null;
    const { rerender } = render(
      <div>
        <HighDensityTable
          data={data}
          columns={columns}
          onOpenFormula={(key) => (inspectedKey = key)}
        />
        <FormulaInspector metricKey={inspectedKey} onClose={() => (inspectedKey = null)} />
      </div>
    );

    const fxBtn = screen.getByTitle('Inspect NetSuite fx Formula');
    fireEvent.click(fxBtn);
    expect(inspectedKey).toBe('party_unpaid_balance');

    rerender(
      <div>
        <HighDensityTable
          data={data}
          columns={columns}
          onOpenFormula={(key) => (inspectedKey = key)}
        />
        <FormulaInspector metricKey={inspectedKey} onClose={() => (inspectedKey = null)} />
      </div>
    );

    expect(screen.getByText('Formula Inspector: Party Unpaid Balance')).toBeInTheDocument();
    expect(screen.getByText('accounts')).toBeInTheDocument();
  });
});

describe('Adversarial Host ERP Integration Static & Contract Verification', () => {
  const hostRoot = path.resolve(__dirname, '../../../shreeshashwatraj-frontend');

  it('verifies ErpAiReporter.js exists, imports UMD bundle, and adds reportGenerated listener', () => {
    const filePath = path.join(hostRoot, 'src/Component/Panels/Admin/AdminReports/ErpAiReporter.js');
    expect(fs.existsSync(filePath)).toBe(true);
    const content = fs.readFileSync(filePath, 'utf-8');

    expect(content).toContain('import "./erp-ai-reporter.umd.js";');
    expect(content).toContain('addEventListener("reportGenerated"');
    expect(content).toContain('<erp-ai-reporter');
    expect(content).toContain('export default function ErpAiReporter');
  });

  it('verifies AiReportsPage.js exists, imports AdminSidebar and ErpAiReporter', () => {
    const filePath = path.join(hostRoot, 'src/Component/Panels/Admin/AdminReports/AiReportsPage.js');
    expect(fs.existsSync(filePath)).toBe(true);
    const content = fs.readFileSync(filePath, 'utf-8');

    expect(content).toContain('import AdminSidebar from "../../../Shared/AdminSidebar/AdminSidebar";');
    expect(content).toContain('import ErpAiReporter from "./ErpAiReporter";');
    expect(content).toContain('export default function AiReportsPage');
  });

  it('verifies route /ai-reports in App.js with PrivateRoute protection', () => {
    const filePath = path.join(hostRoot, 'src/App.js');
    expect(fs.existsSync(filePath)).toBe(true);
    const content = fs.readFileSync(filePath, 'utf-8');

    expect(content).toContain('import AiReportsPage from "./Component/Panels/Admin/AdminReports/AiReportsPage";');
    expect(content).toContain('path="/ai-reports"');
    expect(content).toContain('<PrivateRoute allowedRoles={["admin"]}>');
  });

  it('verifies AdminSidebar.js includes FaBrain icon and link to /ai-reports', () => {
    const filePath = path.join(hostRoot, 'src/Component/Shared/AdminSidebar/AdminSidebar.js');
    expect(fs.existsSync(filePath)).toBe(true);
    const content = fs.readFileSync(filePath, 'utf-8');

    expect(content).toContain('FaBrain');
    expect(content).toContain('path: "/ai-reports"');
    expect(content).toContain('name: "AI Reports"');
  });

  it('verifies Reports.js contains AI Reports navigation tab and renders ErpAiReporter', () => {
    const filePath = path.join(hostRoot, 'src/Component/Panels/Admin/AdminReports/Reports.js');
    expect(fs.existsSync(filePath)).toBe(true);
    const content = fs.readFileSync(filePath, 'utf-8');

    expect(content).toContain('import ErpAiReporter from "./ErpAiReporter";');
    expect(content).toContain('case "ai-reports":');
    expect(content).toContain('<ErpAiReporter isEmbedded={true} />');
    expect(content).toContain('✨ AI Reports');
  });

  it('verifies compiled UMD bundle exists and is >= 900KB', () => {
    const bundlePath = path.join(hostRoot, 'src/Component/Panels/Admin/AdminReports/erp-ai-reporter.umd.js');
    expect(fs.existsSync(bundlePath)).toBe(true);
    const stat = fs.statSync(bundlePath);
    expect(stat.size).toBeGreaterThan(900 * 1024);
  });
});
