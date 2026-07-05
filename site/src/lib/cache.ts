/**
 * CDN cache policy (SPEC §6): TTL == sync cadence (7 days); the weekly
 * Hosting deploy flushes the whole CDN, which is the purge mechanism.
 */
export const CACHE_HEADER = 'public, s-maxage=604800, stale-while-revalidate=86400';

export function setPageCache(response: Response): void {
  response.headers.set('Cache-Control', CACHE_HEADER);
}

export function setNoCache(response: Response): void {
  response.headers.set('Cache-Control', 'no-store');
}
