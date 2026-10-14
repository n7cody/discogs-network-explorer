-- artist_release_roles: one role per (artist, release); Main > Track > Producer > Co-producer > Remix,
-- and every role on an unofficial release becomes Unofficial
CREATE TABLE artist_release_roles AS
WITH credit_roles AS (
    SELECT release_id,
           artist_id,
           lower(trim(unnest(string_split(regexp_replace(role, '\s*\[[^\]]*\]', '', 'g'), ',')))) AS base_role
    FROM credits
    WHERE artist_id IS NOT NULL
),
candidates AS (
    SELECT release_id, artist_id, 'Main' AS role, 1 AS rank FROM release_artists
    UNION ALL
    SELECT release_id, artist_id, 'Track', 2 FROM track_artists
    UNION ALL
    SELECT release_id,
           artist_id,
           CASE base_role WHEN 'producer' THEN 'Producer' WHEN 'co-producer' THEN 'Co-producer' ELSE 'Remix' END,
           CASE base_role WHEN 'producer' THEN 3 WHEN 'co-producer' THEN 4 ELSE 5 END
    FROM credit_roles
    WHERE base_role IN ('producer', 'co-producer', 'remix')
),
best AS (
    SELECT release_id, artist_id, arg_min(role, rank) AS role
    FROM candidates
    WHERE artist_id IS NOT NULL AND artist_id <> 194
    GROUP BY release_id, artist_id
)
SELECT b.artist_id,
       b.release_id,
       CASE WHEN r.is_unofficial THEN 'Unofficial' ELSE b.role END AS role
FROM best b
JOIN releases r USING (release_id)
ORDER BY b.artist_id, b.release_id;

-- label_stats: catalog size, active years and roster size per label (all roles, all years)
CREATE TABLE label_stats AS
SELECT rl.label_id,
       count(DISTINCT rl.release_id) AS catalog_size,
       min(r.year)                   AS earliest_year,
       max(r.year)                   AS latest_year,
       count(DISTINCT arr.artist_id) AS n_artists
FROM release_labels rl
JOIN releases r USING (release_id)
LEFT JOIN artist_release_roles arr USING (release_id)
WHERE rl.label_id IS NOT NULL
GROUP BY rl.label_id
ORDER BY rl.label_id;

-- artist_stats: each artist's full history (all roles, all years)
CREATE TABLE artist_stats AS
SELECT arr.artist_id,
       min(r.year)                    AS earliest_year,
       max(r.year)                    AS latest_year,
       count(DISTINCT arr.release_id) AS n_releases
FROM artist_release_roles arr
JOIN releases r USING (release_id)
GROUP BY arr.artist_id
ORDER BY arr.artist_id;
