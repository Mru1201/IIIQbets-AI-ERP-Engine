export type CaptureDocumentType = 'purchase' | 'sales' | 'expense' | 'credit-note' | 'debit-note';

export interface CaptureField<T = unknown> {
  value: T | null;
  confidence?: number | null;
  corrected?: boolean;
  warning?: string | null;
}

export interface CaptureLine {
  product: CaptureField<string>;
  quantity: CaptureField<number>;
  unit_price: CaptureField<number>;
  total: CaptureField<number>;
  gst_rate: CaptureField<number>;
}

export interface CaptureExtraction {
  document_type: CaptureDocumentType | null;
  supplier_name: CaptureField<string>;
  customer_name: CaptureField<string>;
  supplier_gstin: CaptureField<string>;
  customer_gstin: CaptureField<string>;
  invoice_number: CaptureField<string>;
  note_number: CaptureField<string>;
  invoice_date: CaptureField<string>;
  taxable_value: CaptureField<number>;
  cgst_amount: CaptureField<number>;
  sgst_amount: CaptureField<number>;
  igst_amount: CaptureField<number>;
  total_tax: CaptureField<number>;
  total_amount: CaptureField<number>;
  lines: CaptureLine[];
  warnings: string[];
}

export interface CaptureDocument {
  id: string;
  file_name: string;
  content_type: string;
  status: string;
  extraction: CaptureExtraction | null;
  duplicates: Array<{ reason: string; invoice_number?: string | null }>;
  duplicate_resolution: 'unresolved' | 'not-duplicate' | 'existing-record' | 'force-create';
  created_record_id?: string | number | null;
}

