import React from 'react';
import ReactDOM from 'react-dom/client';
import { AnalyticalListPage } from '../components/AnalyticalListPage';
import cssText from '../styles/index.css?inline';

/**
 * W3C Custom Element: <erp-ai-reporter>
 * Embeds the complete SAP Fiori 3-Zone Analytical List Page with Shadow DOM isolation.
 */
export class ErpAiReporterElement extends HTMLElement {
  private mountPoint: HTMLDivElement | null = null;
  private root: ReactDOM.Root | null = null;

  static get observedAttributes() {
    return ['api-url', 'tenant-id', 'token', 'default-query', 'dev-mode', 'theme'];
  }

  connectedCallback() {
    const shadow = this.shadowRoot || this.attachShadow({ mode: 'open' });

    if (!this.mountPoint) {
      // Inject styles directly into shadow root for complete style encapsulation
      const styleEl = document.createElement('style');
      styleEl.textContent = cssText;
      shadow.appendChild(styleEl);

      this.mountPoint = document.createElement('div');
      this.mountPoint.setAttribute('id', 'erp-ai-reporter-root');
      this.mountPoint.className = 'w-full h-full';
      shadow.appendChild(this.mountPoint);
    }

    this.render();
  }

  disconnectedCallback() {
    if (this.root) {
      this.root.unmount();
      this.root = null;
    }
  }

  attributeChangedCallback(_name: string, oldValue: string, newValue: string) {
    if (oldValue !== newValue && this.mountPoint) {
      this.render();
    }
  }

  private render() {
    if (!this.mountPoint) return;

    const apiUrl = this.getAttribute('api-url') || 'http://localhost:8000';
    const tenantId = this.getAttribute('tenant-id') || 'shree_shashwatraj_agro';
    const token = this.getAttribute('token') || undefined;
    const defaultQuery =
      this.getAttribute('default-query') || 'What is our net profit and gross profit for 2026?';
    const devMode = this.getAttribute('dev-mode') === 'true';
    const theme = this.getAttribute('theme') || undefined;

    if (!this.root) {
      this.root = ReactDOM.createRoot(this.mountPoint);
    }

    this.root.render(
      <React.StrictMode>
        <AnalyticalListPage
          apiUrl={apiUrl}
          tenantId={tenantId}
          token={token}
          defaultQuery={defaultQuery}
          devMode={devMode}
          theme={theme}
          onReportGenerated={(data) => {
            this.dispatchEvent(
              new CustomEvent('reportGenerated', { detail: data, bubbles: true, composed: true })
            );
          }}
          onFormulaInspected={(metricKey) => {
            this.dispatchEvent(
              new CustomEvent('formulaInspected', { detail: metricKey, bubbles: true, composed: true })
            );
          }}
          onExportTriggered={(format) => {
            this.dispatchEvent(
              new CustomEvent('exportTriggered', { detail: format, bubbles: true, composed: true })
            );
          }}
          onFilterChanged={(filters) => {
            this.dispatchEvent(
              new CustomEvent('filterChanged', { detail: filters, bubbles: true, composed: true })
            );
          }}
        />
      </React.StrictMode>
    );
  }
}

// Auto-register Custom Element if not already defined
if (typeof window !== 'undefined' && !customElements.get('erp-ai-reporter')) {
  customElements.define('erp-ai-reporter', ErpAiReporterElement);
}

export default ErpAiReporterElement;
