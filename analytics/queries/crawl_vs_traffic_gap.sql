-- Crawl-vs-traffic gap (SPEC §7.5): pages Google indexes but nobody lands
-- on, joined against the Search Console BigQuery export (enable it too).
-- Replace `analytics_XXXXXX` and `searchconsole` dataset names.
WITH ga_landings AS (
  SELECT
    (SELECT value.string_value FROM UNNEST(event_params) WHERE key = 'page_location') AS url,
    COUNT(*) AS organic_entrances
  FROM `PROJECT.analytics_XXXXXX.events_*`
  WHERE
    event_name = 'session_start'
    AND (SELECT value.string_value FROM UNNEST(event_params) WHERE key = 'source') = 'google'
    AND _TABLE_SUFFIX BETWEEN FORMAT_DATE('%Y%m%d', DATE_SUB(CURRENT_DATE(), INTERVAL 28 DAY))
    AND FORMAT_DATE('%Y%m%d', CURRENT_DATE())
  GROUP BY url
),
gsc AS (
  SELECT url, SUM(impressions) AS impressions, SUM(clicks) AS clicks
  FROM `PROJECT.searchconsole.searchdata_url_impression`
  WHERE data_date >= DATE_SUB(CURRENT_DATE(), INTERVAL 28 DAY)
  GROUP BY url
)
SELECT
  gsc.url,
  gsc.impressions,
  gsc.clicks,
  COALESCE(ga.organic_entrances, 0) AS organic_entrances
FROM gsc
LEFT JOIN ga_landings ga USING (url)
WHERE gsc.impressions > 100 AND COALESCE(ga.organic_entrances, 0) = 0
ORDER BY gsc.impressions DESC
LIMIT 200;
