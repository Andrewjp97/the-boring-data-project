-- Revenue proxy per make (SPEC §7.5): page_views weighted by page kind,
-- from the GA4 BigQuery daily export. Ad impressions correlate with views
-- on year pages (two slots) vs hubs (one slot).
-- Replace `analytics_XXXXXX` with your GA4 export dataset.
SELECT
  (SELECT value.string_value FROM UNNEST(event_params) WHERE key = 'make') AS make,
  (SELECT value.string_value FROM UNNEST(event_params) WHERE key = 'page_kind') AS page_kind,
  COUNT(*) AS page_views,
  COUNT(*) * CASE
    (SELECT value.string_value FROM UNNEST(event_params) WHERE key = 'page_kind')
    WHEN 'year' THEN 2  -- two ad slots
    WHEN 'campaign' THEN 1
    ELSE 1
  END AS ad_impression_proxy
FROM `PROJECT.analytics_XXXXXX.events_*`
WHERE
  event_name = 'page_view'
  AND _TABLE_SUFFIX BETWEEN FORMAT_DATE('%Y%m%d', DATE_SUB(CURRENT_DATE(), INTERVAL 28 DAY))
  AND FORMAT_DATE('%Y%m%d', CURRENT_DATE())
GROUP BY make, page_kind
HAVING make IS NOT NULL
ORDER BY ad_impression_proxy DESC
LIMIT 100;
