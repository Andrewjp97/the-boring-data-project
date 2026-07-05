/**
 * CDN cache policy (SPEC §6): TTL == sync cadence (7 days); the weekly
 * Hosting deploy flushes the whole CDN, which is the purge mechanism.
 */
export const CACHE_HEADER = 'public, s-maxage=604800, stale-while-revalidate=86400';

/** Matches Astro.response, which is a ResponseInit with live headers — not a full Response. */
interface HasHeaders {
  headers: Headers;
}

export function setPageCache(response: HasHeaders): void {
  response.headers.set('Cache-Control', CACHE_HEADER);
}

export function setNoCache(response: HasHeaders): void {
  response.headers.set('Cache-Control', 'no-store');
}
