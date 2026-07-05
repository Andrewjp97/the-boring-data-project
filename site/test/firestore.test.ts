import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  _resetTokenCache,
  collectionForSlug,
  decodeFields,
  decodeValue,
  docIdForSlug,
  getPageDoc,
} from '../src/lib/firestore';

describe('doc addressing', () => {
  it('slug -> doc id', () => {
    expect(docIdForSlug('recalls/honda/cr-v/2016')).toBe('recalls__honda__cr-v__2016');
    expect(docIdForSlug('recall/23V123000')).toBe('recall__23V123000');
  });

  it('slug -> collection', () => {
    expect(collectionForSlug('recalls/honda/cr-v/2016')).toBe('pages');
    expect(collectionForSlug('recalls/honda')).toBe('pages');
    expect(collectionForSlug('recall/23V123000')).toBe('campaignPages');
  });
});

describe('REST value decoding', () => {
  it('decodes every scalar type', () => {
    expect(decodeValue({ stringValue: 'x' })).toBe('x');
    expect(decodeValue({ integerValue: '42' })).toBe(42);
    expect(decodeValue({ doubleValue: 1.5 })).toBe(1.5);
    expect(decodeValue({ booleanValue: true })).toBe(true);
    expect(decodeValue({ nullValue: null })).toBe(null);
    expect(decodeValue({ timestampValue: '2026-07-05T00:00:00Z' })).toBe('2026-07-05T00:00:00Z');
  });

  it('decodes nested maps and arrays (page-doc shape)', () => {
    const fields = {
      slug: { stringValue: 'recalls/honda/cr-v/2016' },
      year: { integerValue: '2016' },
      indexable: { booleanValue: true },
      make: {
        mapValue: {
          fields: { slug: { stringValue: 'honda' }, display: { stringValue: 'Honda' } },
        },
      },
      recalls: {
        arrayValue: {
          values: [
            {
              mapValue: {
                fields: {
                  campno: { stringValue: '23V123000' },
                  affected: { integerValue: '412000' },
                },
              },
            },
          ],
        },
      },
      emptyList: { arrayValue: {} },
    };
    expect(decodeFields(fields)).toEqual({
      slug: 'recalls/honda/cr-v/2016',
      year: 2016,
      indexable: true,
      make: { slug: 'honda', display: 'Honda' },
      recalls: [{ campno: '23V123000', affected: 412000 }],
      emptyList: [],
    });
  });
});

describe('getPageDoc', () => {
  beforeEach(() => {
    _resetTokenCache();
    vi.stubEnv('GCP_PROJECT', 'test-project');
    vi.stubEnv('FIRESTORE_EMULATOR_HOST', 'localhost:8080');
  });
  afterEach(() => {
    vi.unstubAllEnvs();
  });

  it('fetches, decodes, and targets the right collection', async () => {
    const fetchFn = vi.fn(async (url: RequestInfo | URL) => {
      expect(String(url)).toBe(
        'http://localhost:8080/v1/projects/test-project/databases/(default)/documents/pages/recalls__honda__cr-v__2016',
      );
      return new Response(
        JSON.stringify({ fields: { slug: { stringValue: 'recalls/honda/cr-v/2016' } } }),
        { status: 200 },
      );
    });
    const doc = await getPageDoc('recalls/honda/cr-v/2016', fetchFn as typeof fetch);
    expect(doc).toEqual({ slug: 'recalls/honda/cr-v/2016' });
  });

  it('returns null on 404', async () => {
    const fetchFn = vi.fn(async () => new Response('{}', { status: 404 }));
    expect(await getPageDoc('recalls/nope/nope/1999', fetchFn as typeof fetch)).toBeNull();
  });

  it('throws on server errors (so the CDN never caches a broken page)', async () => {
    const fetchFn = vi.fn(async () => new Response('{}', { status: 500 }));
    await expect(getPageDoc('recalls/honda/cr-v/2016', fetchFn as typeof fetch)).rejects.toThrow(
      'Firestore read failed',
    );
  });

  it('caches the metadata-server token across calls', async () => {
    vi.unstubAllEnvs();
    vi.stubEnv('GCP_PROJECT', 'test-project');
    let tokenCalls = 0;
    const fetchFn = vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
      if (String(url).includes('metadata.google.internal')) {
        tokenCalls += 1;
        return new Response(JSON.stringify({ access_token: 'tok', expires_in: 3600 }), {
          status: 200,
        });
      }
      const headers = new Headers(init?.headers);
      expect(headers.get('Authorization')).toBe('Bearer tok');
      return new Response(JSON.stringify({ fields: {} }), { status: 200 });
    });
    await getPageDoc('recalls/honda/cr-v/2016', fetchFn as typeof fetch);
    await getPageDoc('recalls/honda/cr-v/2017', fetchFn as typeof fetch);
    expect(tokenCalls).toBe(1);
  });
});
