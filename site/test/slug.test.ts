import { describe, expect, it } from 'vitest';
import { slugify } from '../src/lib/slug';

// These fixtures are duplicated in etl/tests/test_normalize.py::test_slugify.
// If one side changes, URLs and Firestore doc IDs drift apart — keep in sync.
const PARITY_FIXTURES: Array<[string, string]> = [
  ['CR-V', 'cr-v'],
  ['SILVERADO 1500', 'silverado-1500'],
  ['MERCEDES-BENZ', 'mercedes-benz'],
  ['  F-150  ', 'f-150'],
  ['TOWN & COUNTRY', 'town-and-country'],
  ['ID.4', 'id-4'],
  ['C/K 1500', 'c-k-1500'],
];

describe('slugify parity with ETL', () => {
  it.each(PARITY_FIXTURES)('slugify(%j) -> %j', (input, expected) => {
    expect(slugify(input)).toBe(expected);
  });

  it('handles mixed case from vPIC decode', () => {
    expect(slugify('Honda')).toBe('honda');
    expect(slugify('CX-30')).toBe('cx-30');
  });
});
