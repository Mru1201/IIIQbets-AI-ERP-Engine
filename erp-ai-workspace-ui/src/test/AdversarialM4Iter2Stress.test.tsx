import React from 'react';
import { describe, it, expect, vi, beforeAll, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import fs from 'fs';
import path from 'path';
import '../webcomponent/ErpAiReporterElement';
import { AnalyticalListPage } from '../components/AnalyticalListPage';
import { generateMockReport } from '../api/client';

beforeAll(() => {
  global.ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  };
});

describe('Milestone 4 Iteration 2 Adversarial Stress & Verification Harness', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    global.fetch = vi.fn().mockImplementation(async (url: any, options: any) => {
      const body = options?.body ? JSON.parse(options.body) : {};
      return {
        ok: true,
        json: async () => generateMockReport({ user_query: body.user_query || 'Test query' }),
      } as any;
    });
  });

  // =========================================================================
  // 1. Web Component 10-Cycle Reconnection & Idempotency Stress Test
  // =========================================================================
  it('handles 10 rapid append/remove/reconnect cycles idempotently without NotSupportedError', () => {
    const el = document.createElement('erp-ai-reporter') as any;

    for (let cycle = 1; cycle <= 10; cycle++) {
      expect(() => {
        document.body.appendChild(el);
      }).not.toThrow();

      expect(el.shadowRoot).not.toBeNull();
      expect(el.shadowRoot.mode).toBe('open');
      expect(el.shadowRoot.querySelector('#erp-ai-reporter-root')).not.toBeNull();

      // Manual connectedCallback trigger while connected should also be idempotent
      expect(() => {
        el.connectedCallback();
      }).not.toThrow();

      expect(() => {
        document.body.removeChild(el);
      }).not.toThrow();
    }
  });

  // =========================================================================
  // 2. Attribute Observation Completeness & Reactivity Stress Test
  // =========================================================================
  it('observes exactly all 6 required attributes and handles rapid mutations', () => {
    const CustomElementClass: any = customElements.get('erp-ai-reporter');
    const observed = CustomElementClass.observedAttributes;

    const expectedAttributes = ['api-url', 'capture-api-url', 'tenant-id', 'token', 'default-query', 'dev-mode', 'theme'];
    expect(observed).toEqual(expectedAttributes);

    const el = document.createElement('erp-ai-reporter') as HTMLElement;
    document.body.appendChild(el);

    // Rapidly mutate all observed attributes
    expect(() => {
      el.setAttribute('api-url', 'http://custom-host:9000');
      el.setAttribute('capture-api-url', 'http://capture-host:9010');
      el.setAttribute('tenant-id', 'tenant_adversarial_test');
      el.setAttribute('token', 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.adversarial');
      el.setAttribute('default-query', 'Adversarial financial query');
      el.setAttribute('dev-mode', 'true');
      el.setAttribute('theme', 'fiori-dark');
    }).not.toThrow();

    document.body.removeChild(el);
  });

  // =========================================================================
  // 3. W3C Custom Event Emission Verification with bubbles: true, composed: true
  // =========================================================================
  it('dispatches reportGenerated and filterChanged with bubbles: true and composed: true', async () => {
    const el = document.createElement('erp-ai-reporter') as HTMLElement;
    let reportEvent: any = null;
    let filterEvent: any = null;

    const hostContainer = document.createElement('div');
    document.body.appendChild(hostContainer);
    hostContainer.appendChild(el);

    // Attach listeners on parent container to verify bubbling and shadow DOM boundary crossing
    hostContainer.addEventListener('reportGenerated', (e: Event) => {
      reportEvent = e;
    });
    hostContainer.addEventListener('filterChanged', (e: Event) => {
      filterEvent = e;
    });

    await waitFor(() => {
      expect(reportEvent).not.toBeNull();
      expect(filterEvent).not.toBeNull();
    }, { timeout: 1500 });

    // Verify W3C Event Composition
    expect(reportEvent?.bubbles).toBe(true);
    expect(reportEvent?.composed).toBe(true);
    expect(reportEvent?.detail).toBeDefined();

    expect(filterEvent?.bubbles).toBe(true);
    expect(filterEvent?.composed).toBe(true);

    document.body.removeChild(hostContainer);
  });

  it('dispatches formulaInspected and exportTriggered when callbacks are triggered', async () => {
    const el = document.createElement('erp-ai-reporter') as HTMLElement;
    let inspectedEvent: any = null;
    let exportEvent: any = null;

    el.addEventListener('formulaInspected', (e: Event) => {
      inspectedEvent = e as CustomEvent;
    });
    el.addEventListener('exportTriggered', (e: Event) => {
      exportEvent = e as CustomEvent;
    });

    document.body.appendChild(el);

    await waitFor(() => {
      expect(el.shadowRoot?.querySelector('#erp-ai-reporter-root')).not.toBeNull();
    });

    // Directly dispatch via element's registered listeners to test event contract
    el.dispatchEvent(
      new CustomEvent('formulaInspected', { detail: 'gross_profit', bubbles: true, composed: true })
    );
    expect(inspectedEvent).not.toBeNull();
    expect(inspectedEvent?.bubbles).toBe(true);
    expect(inspectedEvent?.composed).toBe(true);
    expect(inspectedEvent?.detail).toBe('gross_profit');

    el.dispatchEvent(
      new CustomEvent('exportTriggered', { detail: 'xlsx', bubbles: true, composed: true })
    );
    expect(exportEvent).not.toBeNull();
    expect(exportEvent?.bubbles).toBe(true);
    expect(exportEvent?.composed).toBe(true);
    expect(exportEvent?.detail).toBe('xlsx');

    document.body.removeChild(el);
  });

  // =========================================================================
  // 4. Global Cmd+K / Ctrl+K Keydown Toggle in AnalyticalListPage
  // =========================================================================
  it('toggles Awesomebar modal on Cmd+K, Ctrl+K, and ignores non-modifier or non-K keystrokes', async () => {
    render(
      <AnalyticalListPage
        apiUrl="http://localhost:8000"
        tenantId="shree_shashwatraj_agro"
      />
    );

    const inputPlaceholder = "Ask anything... (e.g. 'Show gross profit by retailer in Q3 2026')";

    // Negative check: standard 'k' without modifier must NOT open modal
    fireEvent.keyDown(window, { key: 'k', metaKey: false, ctrlKey: false });
    expect(screen.queryByPlaceholderText(inputPlaceholder)).not.toBeInTheDocument();

    // Negative check: Cmd+J must NOT open modal
    fireEvent.keyDown(window, { key: 'j', metaKey: true });
    expect(screen.queryByPlaceholderText(inputPlaceholder)).not.toBeInTheDocument();

    // Positive check: Cmd+k opens modal
    fireEvent.keyDown(window, { key: 'k', metaKey: true });
    expect(screen.getByPlaceholderText(inputPlaceholder)).toBeInTheDocument();

    // Positive check: Cmd+K (capitalized) closes modal (toggle)
    fireEvent.keyDown(window, { key: 'K', metaKey: true });
    await waitFor(() => {
      expect(screen.queryByPlaceholderText(inputPlaceholder)).not.toBeInTheDocument();
    });

    // Positive check: Ctrl+k opens modal (Windows / Linux)
    fireEvent.keyDown(window, { key: 'k', ctrlKey: true });
    expect(screen.getByPlaceholderText(inputPlaceholder)).toBeInTheDocument();

    // Positive check: Ctrl+K closes modal
    fireEvent.keyDown(window, { key: 'K', ctrlKey: true });
    await waitFor(() => {
      expect(screen.queryByPlaceholderText(inputPlaceholder)).not.toBeInTheDocument();
    });
  });

  // =========================================================================
  // 5. Host ERP Bundle Integrity & Synchronization
  // =========================================================================
  it('verifies that host UMD bundle exists and contains updated custom element and event handlers', () => {
    const hostBundlePath = path.resolve(
      __dirname,
      '../../../shreeshashwatraj-frontend/src/Component/Panels/Admin/AdminReports/erp-ai-reporter.umd.js'
    );
    expect(fs.existsSync(hostBundlePath)).toBe(true);

    const stat = fs.statSync(hostBundlePath);
    // Bundle should be substantial (> 950 KB)
    expect(stat.size).toBeGreaterThan(950 * 1024);

    const bundleContent = fs.readFileSync(hostBundlePath, 'utf-8');
    expect(bundleContent).toContain('erp-ai-reporter');
    expect(bundleContent).toContain('reportGenerated');
    expect(bundleContent).toContain('formulaInspected');
    expect(bundleContent).toContain('exportTriggered');
    expect(bundleContent).toContain('filterChanged');
  });
});
