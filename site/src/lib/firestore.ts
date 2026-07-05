/**
 * Firestore access via the REST API (SPEC §6).
 *
 * One GET per page render, authenticated with a cached access token from the
 * Cloud Run metadata server — no Admin SDK in the container, so cold starts
 * stay fast. Local dev paths: FIRESTORE_EMULATOR_HOST (no auth) or a token in
 * FIRESTORE_ACCESS_TOKEN (`gcloud auth print-access-token`).
 */

const METADATA_TOKEN_URL =
  'http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token';

interface CachedToken {
  token: string;
  expiresAt: number; // epoch ms
}

let cachedToken: CachedToken | null = null;

function projectId(): string {
  const id = process.env.GCP_PROJECT || process.env.GOOGLE_CLOUD_PROJECT;
  if (!id) throw new Error('GCP_PROJECT / GOOGLE_CLOUD_PROJECT not set');
  return id;
}

async function getAccessToken(fetchFn: typeof fetch = fetch): Promise<string | null> {
  if (process.env.FIRESTORE_EMULATOR_HOST) return null;
  if (process.env.FIRESTORE_ACCESS_TOKEN) return process.env.FIRESTORE_ACCESS_TOKEN;
  if (cachedToken && Date.now() < cachedToken.expiresAt - 60_000) return cachedToken.token;
  const resp = await fetchFn(METADATA_TOKEN_URL, {
    headers: { 'Metadata-Flavor': 'Google' },
  });
  if (!resp.ok) throw new Error(`metadata server token fetch failed: ${resp.status}`);
  const body = (await resp.json()) as { access_token: string; expires_in: number };
  cachedToken = {
    token: body.access_token,
    expiresAt: Date.now() + body.expires_in * 1000,
  };
  return cachedToken.token;
}

function baseUrl(): string {
  const emulator = process.env.FIRESTORE_EMULATOR_HOST;
  const host = emulator ? `http://${emulator}` : 'https://firestore.googleapis.com';
  return `${host}/v1/projects/${projectId()}/databases/(default)/documents`;
}

// --- Firestore REST value decoding -----------------------------------------

type FirestoreValue = Record<string, unknown>;

export function decodeValue(value: FirestoreValue): unknown {
  if ('nullValue' in value) return null;
  if ('stringValue' in value) return value.stringValue;
  if ('booleanValue' in value) return value.booleanValue;
  if ('integerValue' in value) return Number(value.integerValue);
  if ('doubleValue' in value) return value.doubleValue;
  if ('timestampValue' in value) return value.timestampValue;
  if ('arrayValue' in value) {
    const arr = value.arrayValue as { values?: FirestoreValue[] };
    return (arr.values ?? []).map(decodeValue);
  }
  if ('mapValue' in value) {
    const map = value.mapValue as { fields?: Record<string, FirestoreValue> };
    return decodeFields(map.fields ?? {});
  }
  return null; // bytes/reference/geopoint never appear in page docs
}

export function decodeFields(fields: Record<string, FirestoreValue>): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(fields)) out[key] = decodeValue(value);
  return out;
}

// --- Page-document fetch -----------------------------------------------------

export function docIdForSlug(slug: string): string {
  return slug.replaceAll('/', '__');
}

export function collectionForSlug(slug: string): string {
  return slug.startsWith('recall/') ? 'campaignPages' : 'pages';
}

/** Fetch one page document by URL slug. Returns null on 404 (SPEC §6). */
export async function getPageDoc(
  slug: string,
  fetchFn: typeof fetch = fetch,
): Promise<Record<string, unknown> | null> {
  const url = `${baseUrl()}/${collectionForSlug(slug)}/${encodeURIComponent(docIdForSlug(slug))}`;
  const headers: Record<string, string> = {};
  const token = await getAccessToken(fetchFn);
  if (token) headers.Authorization = `Bearer ${token}`;
  const resp = await fetchFn(url, { headers });
  if (resp.status === 404) return null;
  if (!resp.ok) throw new Error(`Firestore read failed for ${slug}: ${resp.status}`);
  const body = (await resp.json()) as { fields?: Record<string, FirestoreValue> };
  return decodeFields(body.fields ?? {});
}

/** Test hook. */
export function _resetTokenCache(): void {
  cachedToken = null;
}
