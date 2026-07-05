-- Which components correlate with affiliate clicks (SPEC §7.5)?
-- Drives which ASIN groups to expand in site/src/data/affiliate-map.json.
-- Replace `analytics_XXXXXX` with your GA4 export dataset.
SELECT
  (SELECT value.string_value FROM UNNEST(event_params) WHERE key = 'component') AS component,
  (SELECT value.string_value FROM UNNEST(event_params) WHERE key = 'asin_group') AS asin_group,
  COUNT(*) AS clicks,
  COUNT(DISTINCT user_pseudo_id) AS unique_clickers
FROM `PROJECT.analytics_XXXXXX.events_*`
WHERE
  event_name = 'affiliate_click'
  AND _TABLE_SUFFIX BETWEEN FORMAT_DATE('%Y%m%d', DATE_SUB(CURRENT_DATE(), INTERVAL 28 DAY))
  AND FORMAT_DATE('%Y%m%d', CURRENT_DATE())
GROUP BY component, asin_group
ORDER BY clicks DESC
LIMIT 100;
