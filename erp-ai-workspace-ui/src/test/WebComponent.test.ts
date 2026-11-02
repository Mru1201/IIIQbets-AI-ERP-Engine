import { describe, it, expect } from 'vitest';
import '../webcomponent/ErpAiReporterElement';

describe('WebComponent <erp-ai-reporter>', () => {
  it('registers erp-ai-reporter custom element in customElements registry', () => {
    const CustomElementClass = customElements.get('erp-ai-reporter');
    expect(CustomElementClass).toBeDefined();
  });

  it('instantiates custom element and attaches Shadow DOM with style encapsulation', () => {
    const el = document.createElement('erp-ai-reporter') as HTMLElement;
    el.setAttribute('api-url', 'http://localhost:8000');
    el.setAttribute('tenant-id', 'test_tenant');

    document.body.appendChild(el);

    expect(el.shadowRoot).not.toBeNull();
    const shadow = el.shadowRoot!;

    // Check style encapsulation
    const styleEl = shadow.querySelector('style');
    expect(styleEl).not.toBeNull();

    // Check root mount point
    const rootEl = shadow.querySelector('#erp-ai-reporter-root');
    expect(rootEl).not.toBeNull();

    document.body.removeChild(el);
  });

  it('observes attributes api-url, tenant-id, token, default-query, dev-mode, theme', () => {
    const CustomElementClass: any = customElements.get('erp-ai-reporter');
    expect(CustomElementClass.observedAttributes).toEqual([
      'api-url',
      'tenant-id',
      'token',
      'default-query',
      'dev-mode',
      'theme',
    ]);
  });

  it('supports idempotent reconnection without throwing NotSupportedError', () => {
    const el = document.createElement('erp-ai-reporter') as any;
    document.body.appendChild(el);
    expect(el.shadowRoot).not.toBeNull();

    // Reconnecting or calling connectedCallback multiple times must not throw
    expect(() => {
      el.connectedCallback();
    }).not.toThrow();

    document.body.removeChild(el);
    document.body.appendChild(el);
    expect(el.shadowRoot).not.toBeNull();
    document.body.removeChild(el);
  });
});
