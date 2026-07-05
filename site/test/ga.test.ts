import { describe, expect, it } from 'vitest';
import { buildPageDims, complaintCountBucket, recallCountBucket } from '../src/lib/ga';

describe('GA4 buckets (registered as custom dimensions, SPEC §7.3)', () => {
  it('recall buckets: 0 / 1-2 / 3-5 / 6+', () => {
    expect(recallCountBucket(0)).toBe('0');
    expect(recallCountBucket(1)).toBe('1-2');
    expect(recallCountBucket(2)).toBe('1-2');
    expect(recallCountBucket(3)).toBe('3-5');
    expect(recallCountBucket(5)).toBe('3-5');
    expect(recallCountBucket(6)).toBe('6+');
    expect(recallCountBucket(40)).toBe('6+');
  });

  it('complaint buckets', () => {
    expect(complaintCountBucket(0)).toBe('0');
    expect(complaintCountBucket(9)).toBe('1-9');
    expect(complaintCountBucket(10)).toBe('10-49');
    expect(complaintCountBucket(50)).toBe('50+');
  });

  it('buildPageDims assembles all seven custom dimensions', () => {
    const dims = buildPageDims({
      kind: 'year',
      make: 'honda',
      model: 'cr-v',
      year: 2016,
      recallCount: 4,
      complaintTotal: 187,
      indexable: true,
    });
    expect(dims).toEqual({
      page_kind: 'year',
      make: 'honda',
      model: 'cr-v',
      model_year: 2016,
      recall_count_bucket: '3-5',
      complaint_count_bucket: '50+',
      indexable: 'true',
    });
  });

  it('noindex pages report indexable=false for crawl-gap analysis', () => {
    expect(buildPageDims({ kind: 'year', indexable: false }).indexable).toBe('false');
  });

  it('omits inapplicable entity dims instead of sending nulls to gtag', () => {
    const dims = buildPageDims({ kind: 'home' });
    expect(dims).toEqual({
      page_kind: 'home',
      recall_count_bucket: '0',
      complaint_count_bucket: '0',
      indexable: 'true',
    });
    expect('make' in dims).toBe(false);
    expect('model' in dims).toBe(false);
    expect('model_year' in dims).toBe(false);
  });
});
