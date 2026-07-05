/**
 * GA4 helpers (SPEC §7). The actual gtag snippet is inlined by
 * Analytics.astro; this module holds the pure logic so it's unit-testable:
 * custom-dimension assembly and count bucketing.
 */

export type PageKind = 'year' | 'model' | 'make' | 'campaign' | 'home' | 'utility';

export interface PageDims {
  page_kind: PageKind;
  /** Absent (not null) when not applicable — gtag must never receive nulls;
   *  GA4 reports the dimension as "(not set)" either way. */
  make?: string;
  model?: string;
  model_year?: number;
  recall_count_bucket: string;
  complaint_count_bucket: string;
  indexable: 'true' | 'false';
}

export function recallCountBucket(n: number): string {
  if (n <= 0) return '0';
  if (n <= 2) return '1-2';
  if (n <= 5) return '3-5';
  return '6+';
}

export function complaintCountBucket(n: number): string {
  if (n <= 0) return '0';
  if (n <= 9) return '1-9';
  if (n <= 49) return '10-49';
  return '50+';
}

export interface PageDimsInput {
  kind: PageKind;
  make?: string | null;
  model?: string | null;
  year?: number | null;
  recallCount?: number;
  complaintTotal?: number;
  indexable?: boolean;
}

export function buildPageDims(input: PageDimsInput): PageDims {
  const dims: PageDims = {
    page_kind: input.kind,
    recall_count_bucket: recallCountBucket(input.recallCount ?? 0),
    complaint_count_bucket: complaintCountBucket(input.complaintTotal ?? 0),
    indexable: input.indexable === false ? 'false' : 'true',
  };
  if (input.make != null) dims.make = input.make;
  if (input.model != null) dims.model = input.model;
  if (input.year != null) dims.model_year = input.year;
  return dims;
}

/** Event names sent via delegated listeners — registered once in GA4 admin. */
export const EVENTS = {
  affiliateClick: 'affiliate_click',
  vinDecode: 'vin_decode',
  outboundNhtsa: 'outbound_nhtsa',
  searchUsed: 'search_used',
  relatedLinkClick: 'related_link_click',
} as const;
