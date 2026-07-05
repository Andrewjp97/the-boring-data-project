/**
 * URL slug rules — MUST stay in lockstep with etl/src/etl/normalize.py
 * slugify(). Parity is pinned by test/slug.test.ts using the same fixtures
 * as the Python test suite.
 */
export function slugify(value: string): string {
  let slug = value.trim().toLowerCase();
  slug = slug.replaceAll('&', ' and ');
  slug = slug.replace(/[^a-z0-9]+/g, '-');
  return slug.replace(/^-+|-+$/g, '');
}
