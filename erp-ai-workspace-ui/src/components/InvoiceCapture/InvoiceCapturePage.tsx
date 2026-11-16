import React, { useEffect, useState } from 'react';
import { AlertTriangle, CheckCircle2, FileCheck2, FileText, Loader2, UploadCloud } from 'lucide-react';
import {
  confirmCaptureDocument,
  extractCaptureDocument,
  uploadCaptureDocument,
} from '../../api/client';
import { CaptureDocument, CaptureDocumentType, CaptureExtraction } from '../../types/capture';

interface InvoiceCapturePageProps {
  tenantId: string;
  token?: string;
  captureApiUrl?: string;
}

const fieldValue = (extraction: CaptureExtraction | null, key: keyof CaptureExtraction): string => {
  const field = extraction?.[key];
  return field && typeof field === 'object' && 'value' in field ? String(field.value ?? '') : '';
};

const confidenceLabel = (confidence?: number | null): string => {
  if (confidence == null) return 'Needs review';
  return `${Math.round(confidence * 100)}% confidence`;
};

type CaptureFieldKey = Exclude<keyof CaptureExtraction, 'document_type' | 'lines' | 'warnings'>;

export const InvoiceCapturePage: React.FC<InvoiceCapturePageProps> = ({
  tenantId,
  token,
  captureApiUrl,
}) => {
  const [document, setDocument] = useState<CaptureDocument | null>(null);
  const [extraction, setExtraction] = useState<CaptureExtraction | null>(null);
  const [documentType, setDocumentType] = useState<CaptureDocumentType>('purchase');
  const [duplicateResolution, setDuplicateResolution] = useState<CaptureDocument['duplicate_resolution']>('not-duplicate');
  const [message, setMessage] = useState('Upload one PDF or phone photo to begin.');
  const [busy, setBusy] = useState(false);
  const [sourcePreviewUrl, setSourcePreviewUrl] = useState<string | null>(null);
  const [sourceContentType, setSourceContentType] = useState<string | null>(null);
  const hasError = /failed|error|timed out/i.test(message);

  useEffect(() => {
    return () => {
      if (sourcePreviewUrl) URL.revokeObjectURL(sourcePreviewUrl);
    };
  }, [sourcePreviewUrl]);

  const updateField = (key: keyof CaptureExtraction, value: string) => {
    setExtraction((current) => {
      if (!current) return current;
      const field = current[key];
      if (!field || typeof field !== 'object' || !('value' in field)) return current;
      return { ...current, [key]: { ...field, value, corrected: true } };
    });
  };

  const handleUpload = async (file: File) => {
    setBusy(true);
    if (sourcePreviewUrl) URL.revokeObjectURL(sourcePreviewUrl);
    setSourcePreviewUrl(URL.createObjectURL(file));
    setSourceContentType(file.type);
    setMessage('Uploading scan...');
    try {
      const uploaded = await uploadCaptureDocument(file, tenantId, token, captureApiUrl);
      setDocument(uploaded);
      setMessage('Extracting GST fields...');
      const extracted = await extractCaptureDocument(uploaded.id, tenantId, token, captureApiUrl);
      setDocument(extracted);
      setExtraction(extracted.extraction);
      if (extracted.extraction?.document_type) setDocumentType(extracted.extraction.document_type);
      setMessage('Review extracted fields, resolve warnings, then confirm creation.');
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'Invoice capture failed.');
    } finally {
      setBusy(false);
    }
  };

  const handleConfirm = async () => {
    if (!document || !extraction) return;
    setBusy(true);
    setMessage('Confirming and creating the ERP record...');
    try {
      const result = await confirmCaptureDocument(
        document.id,
        extraction,
        documentType,
        duplicateResolution,
        tenantId,
        token,
        captureApiUrl
      );
      setDocument(result.document);
      setMessage(`Created ${documentType} record ${result.document.created_record_id ?? ''}. Source scan retained.`);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'ERP creation failed.');
    } finally {
      setBusy(false);
    }
  };

  const updateLine = (index: number, key: 'product' | 'quantity' | 'unit_price' | 'total', value: string) => {
    setExtraction((current) => {
      if (!current) return current;
      const lines = current.lines.map((line, lineIndex) =>
        lineIndex === index
          ? { ...line, [key]: { ...line[key], value, corrected: true } }
          : line
      );
      return { ...current, lines };
    });
  };

  return (
    <section className="min-h-[calc(100vh-56px)] bg-[#F5F6F7] p-4 lg:p-6">
      <div className="mb-4 flex items-center justify-between">
        <div>
          <h1 className="text-sm font-bold text-fiori-dark">AI Invoice Capture</h1>
          <p className="text-xs text-slate-500">Human-reviewed GST-first capture for Shree Agro</p>
        </div>
        <label className="inline-flex cursor-pointer items-center rounded-lg bg-fiori-blue px-3 py-2 text-xs font-semibold text-white">
          <UploadCloud className="mr-2 h-4 w-4" />
          Upload PDF/photo
          <input
            type="file"
            accept="application/pdf,image/jpeg,image/png,image/webp"
            className="hidden"
            disabled={busy}
            onChange={(event) => {
              const file = event.target.files?.[0];
              if (file) void handleUpload(file);
            }}
          />
        </label>
      </div>
      <div className="mb-4 flex flex-wrap items-center gap-2 text-[11px] font-medium text-slate-500">
        {['Upload', 'Extract', 'Review', 'Confirm'].map((step, index) => (
          <React.Fragment key={step}>
            {index > 0 && <span className="text-slate-300">/</span>}
            <span className={document?.status === 'created' && index < 4 ? 'text-emerald-700' : index < 2 && document ? 'text-fiori-blue' : ''}>
              {document?.status === 'created' && index < 4 && <CheckCircle2 className="mr-1 inline h-3 w-3" />}
              {step}
            </span>
          </React.Fragment>
        ))}
      </div>
      <div className="grid gap-4 lg:grid-cols-[minmax(220px,0.7fr)_minmax(220px,0.8fr)_minmax(420px,1.5fr)]">
        <aside className="rounded-lg border border-fiori-border bg-white p-4 shadow-xs">
          <div className="mb-3 flex items-center text-xs font-semibold text-fiori-dark">
            <FileCheck2 className="mr-2 h-4 w-4 text-fiori-blue" /> Capture copilot
          </div>
          <p className={`text-xs leading-5 ${hasError ? 'font-medium text-red-700' : 'text-slate-600'}`} role={hasError ? 'alert' : undefined}>
            {message}
          </p>
          {document && <p className="mt-4 break-all text-[11px] text-slate-400">{document.file_name}</p>}
          {busy && <Loader2 className="mt-4 h-4 w-4 animate-spin text-fiori-blue" />}
        </aside>
        <aside className="rounded-lg border border-fiori-border bg-slate-50 p-3 shadow-xs">
          <div className="mb-3 flex items-center text-xs font-semibold text-fiori-dark">
            <FileText className="mr-2 h-4 w-4 text-fiori-blue" /> Source scan
          </div>
          {sourcePreviewUrl && sourceContentType?.startsWith('image/') ? (
            <img src={sourcePreviewUrl} alt="Uploaded invoice preview" className="max-h-80 w-full rounded border border-slate-200 object-contain" />
          ) : sourcePreviewUrl ? (
            <object data={sourcePreviewUrl} type={sourceContentType || 'application/pdf'} className="h-80 w-full rounded border border-slate-200" aria-label="Uploaded invoice preview">
              <p className="p-3 text-xs text-slate-500">PDF preview unavailable in this browser.</p>
            </object>
          ) : (
            <p className="text-xs leading-5 text-slate-500">The uploaded PDF or photo will stay visible here while you review the extracted values.</p>
          )}
        </aside>
        <div className="rounded-lg border border-fiori-border bg-white p-4 shadow-xs">
          <div className="mb-4 flex items-center justify-between">
            <h2 className="text-xs font-bold text-fiori-dark">Editable confirmation</h2>
            <select
              value={documentType}
              onChange={(event) => setDocumentType(event.target.value as CaptureDocumentType)}
              className="rounded border border-slate-300 px-2 py-1 text-xs"
              disabled={busy}
            >
              <option value="purchase">Purchase</option>
              <option value="sales">Sales</option>
              <option value="expense">Expense</option>
              <option value="credit-note">Credit note</option>
              <option value="debit-note">Debit note</option>
            </select>
          </div>
          {!extraction ? (
            <p className="text-xs text-slate-400">Extracted fields will appear here for correction.</p>
          ) : (
            <>
              <div className="grid gap-3 md:grid-cols-2">
                {([
                  ['invoice_number', 'Invoice number'],
                  ['note_number', 'Note number'],
                  ['invoice_date', 'Invoice date'],
                  ['supplier_name', 'Supplier'],
                  ['customer_name', 'Customer'],
                  ['supplier_gstin', 'Supplier GSTIN'],
                  ['customer_gstin', 'Customer GSTIN'],
                  ['taxable_value', 'Taxable value'],
                  ['total_tax', 'Total tax'],
                  ['total_amount', 'Total amount'],
                  ['cgst_amount', 'CGST'],
                  ['sgst_amount', 'SGST'],
                  ['igst_amount', 'IGST'],
                ] as Array<[CaptureFieldKey, string]>).map(([key, label]) => (
                  <label key={key} className="text-[11px] font-medium text-slate-600">
                    <span className="flex items-center justify-between">
                      {label}
                      <span className="font-normal text-slate-400">{confidenceLabel(extraction[key]?.confidence)}</span>
                    </span>
                    <input
                      value={fieldValue(extraction, key)}
                      onChange={(event) => updateField(key, event.target.value)}
                      className="mt-1 w-full rounded border border-slate-300 px-2 py-1.5 text-xs text-slate-800"
                      disabled={busy}
                    />
                    {extraction[key]?.warning && (
                      <span className="mt-1 flex items-center text-[10px] font-normal text-amber-700">
                        <AlertTriangle className="mr-1 h-3 w-3" /> {extraction[key]?.warning}
                      </span>
                    )}
                  </label>
                ))}
              </div>
              {extraction.lines.length > 0 && (
                <div className="mt-5 overflow-x-auto">
                  <h3 className="mb-2 text-[11px] font-bold text-fiori-dark">Line items</h3>
                  <table className="w-full min-w-[560px] text-left text-[11px]">
                    <thead className="border-b border-slate-200 text-slate-500">
                      <tr><th className="pb-2">Product</th><th className="pb-2">Qty</th><th className="pb-2">Unit price</th><th className="pb-2">Total</th></tr>
                    </thead>
                    <tbody>
                      {extraction.lines.map((line, index) => (
                        <tr key={index} className="border-b border-slate-100">
                          {(['product', 'quantity', 'unit_price', 'total'] as const).map((key) => (
                            <td key={key} className="py-2 pr-2">
                              <input
                                aria-label={`${key} line ${index + 1}`}
                                value={String(line[key]?.value ?? '')}
                                onChange={(event) => updateLine(index, key, event.target.value)}
                                className="w-full rounded border border-slate-300 px-2 py-1 text-xs"
                                disabled={busy}
                              />
                            </td>
                          ))}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
              {extraction.warnings.length > 0 && (
                <div className="mt-4 rounded border border-amber-200 bg-amber-50 p-3 text-xs text-amber-800">
                  {extraction.warnings.join(' ')}
                </div>
              )}
              {document?.duplicates?.length ? (
                <label className="mt-4 block text-xs text-amber-800">
                  Possible duplicate detected. Resolution
                  <select
                    value={duplicateResolution}
                    onChange={(event) => setDuplicateResolution(event.target.value as CaptureDocument['duplicate_resolution'])}
                    className="ml-2 rounded border border-amber-300 px-2 py-1"
                  >
                    <option value="not-duplicate">Not a duplicate</option>
                    <option value="existing-record">Use existing record</option>
                    <option value="force-create">Force create</option>
                  </select>
                </label>
              ) : null}
              <button
                type="button"
                onClick={() => void handleConfirm()}
                disabled={busy || !document}
                className="mt-5 rounded-lg bg-fiori-blue px-4 py-2 text-xs font-semibold text-white disabled:cursor-not-allowed disabled:opacity-50"
              >
                Confirm and create ERP record
              </button>
            </>
          )}
        </div>
      </div>
    </section>
  );
};

