-- Queries for local_db.py, one per `-- name:` header. Parameters are `$name` placeholders.
-- ID lists arrive as a list parameter and are turned into a one-column table with unnest(), then
-- joined; DuckDB then reads only the parts of the (sorted) tables that hold those IDs.

-- name: seed_pool
-- Seed Labels -> artist pool, in discovery order (first Seed Release, then artist ID).
-- Track Artists count except on DJ mixes that are not Various Artists releases.
-- Only original releases count (no master, or the master's main release).
-- $seed_label_ids BIGINT[], $min_year, $max_year INTEGER (NULL = open)
WITH seed_labels AS (
    SELECT DISTINCT unnest($seed_label_ids) AS label_id
),
seed_releases AS (
    SELECT DISTINCT r.release_id
    FROM seed_labels
    JOIN release_labels rl USING (label_id)
    JOIN releases r USING (release_id)
    WHERE (r.year IS NULL OR r.year BETWEEN coalesce($min_year, 0) AND coalesce($max_year, 9999))
      AND (r.master_id IS NULL OR r.is_main_release)
),
various_artists_releases AS (
    SELECT ra.release_id
    FROM seed_releases
    JOIN release_artists ra USING (release_id)
    GROUP BY ra.release_id
    HAVING bool_and(ra.artist_id = 194)
),
mixed_releases AS (
    -- DJ mixes: their Track Artists are the tracks played, not contributors
    SELECT DISTINCT f.release_id
    FROM seed_releases
    JOIN release_formats f USING (release_id)
    WHERE list_contains(f.descriptions, 'Mixed')
)
SELECT CAST(arr.artist_id AS VARCHAR) AS artist_id
FROM seed_releases
JOIN artist_release_roles arr USING (release_id)
WHERE arr.role <> 'Track'
   OR arr.release_id IN (SELECT release_id FROM various_artists_releases)
   OR arr.release_id NOT IN (SELECT release_id FROM mixed_releases)
GROUP BY arr.artist_id
ORDER BY min(arr.release_id), arr.artist_id;

-- name: label_rows
-- Artist pool -> one row per (artist, release, label), all years, no caps.
-- Only original releases count (no master, or the master's main release).
-- $artist_ids BIGINT[]
WITH pool AS (
    SELECT DISTINCT unnest($artist_ids) AS artist_id
),
pool_releases AS (
    SELECT arr.artist_id, arr.release_id, arr.role
    FROM pool
    JOIN artist_release_roles arr USING (artist_id)
),
pool_release_ids AS (
    SELECT DISTINCT release_id
    FROM pool_releases
),
release_label_links AS (
    -- one row per (release, label); a label listed with several catalog numbers repeats
    SELECT rl.release_id, rl.label_id, min(rl.label_name) AS label_name
    FROM pool_release_ids
    JOIN release_labels rl USING (release_id)
    WHERE rl.label_id IS NOT NULL
      AND lower(rl.label_name) NOT LIKE 'not on label%'
    GROUP BY rl.release_id, rl.label_id
),
pool_formats AS (
    SELECT f.release_id, f.name, f.descriptions
    FROM pool_release_ids
    JOIN release_formats f USING (release_id)
),
release_format_names AS (
    SELECT release_id,
           string_agg(DISTINCT name, ', ' ORDER BY name) AS format_names
    FROM pool_formats
    GROUP BY release_id
),
release_format_descriptions AS (
    SELECT release_id,
           string_agg(DISTINCT description, ', ' ORDER BY description) AS format_descriptions
    FROM (SELECT release_id, unnest(descriptions) AS description FROM pool_formats) AS format_description_list
    GROUP BY release_id
)
SELECT CAST(p.artist_id AS VARCHAR)                     AS artist_id,
       coalesce(a.name, 'artist_' || p.artist_id)      AS artist_name,
       CAST(p.release_id AS VARCHAR)                    AS release_id,
       coalesce(r.title, '')                            AS release_title,
       p.role,
       coalesce(fn.format_names, '')                    AS format_names,
       coalesce(fd.format_descriptions, '')             AS format_descriptions,
       coalesce(array_to_string(r.genres, ', '), '')    AS genres,
       coalesce(array_to_string(r.styles, ', '), '')    AS styles,
       coalesce(r.country, '')                          AS country,
       r.year,
       CAST(rl.label_id AS VARCHAR)                     AS label_id,
       rl.label_name
FROM pool_releases p
JOIN releases r USING (release_id)
JOIN release_label_links rl USING (release_id)
LEFT JOIN artists a ON a.artist_id = p.artist_id
LEFT JOIN release_format_names fn ON fn.release_id = p.release_id
LEFT JOIN release_format_descriptions fd ON fd.release_id = p.release_id
WHERE r.master_id IS NULL OR r.is_main_release
ORDER BY p.artist_id, p.release_id, rl.label_id;

-- name: label_stats
-- Catalog size, active years and roster size for each requested label (labels without releases are absent).
-- A few labels appear on releases but not in the labels file; their name comes from the releases.
-- $label_ids BIGINT[]
WITH requested_labels AS (
    SELECT DISTINCT unnest($label_ids) AS label_id
),
names_on_releases AS (
    SELECT rl.label_id, min(rl.label_name) AS label_name
    FROM requested_labels
    JOIN release_labels rl USING (label_id)
    GROUP BY rl.label_id
)
SELECT CAST(s.label_id AS VARCHAR)       AS label_id,
       coalesce(l.name, nr.label_name)   AS label_name,
       s.catalog_size,
       s.earliest_year,
       s.latest_year,
       s.n_artists
FROM requested_labels
JOIN label_stats s USING (label_id)
LEFT JOIN labels l USING (label_id)
LEFT JOIN names_on_releases nr USING (label_id)
ORDER BY s.label_id;

-- name: artist_stats
-- Each requested artist's full history: active years and number of releases.
-- $artist_ids BIGINT[]
WITH requested_artists AS (
    SELECT DISTINCT unnest($artist_ids) AS artist_id
)
SELECT CAST(s.artist_id AS VARCHAR) AS artist_id,
       s.earliest_year,
       s.latest_year,
       s.n_releases
FROM requested_artists
JOIN artist_stats s USING (artist_id)
ORDER BY s.artist_id;

-- name: artist_window_counts
-- Each requested artist's releases (any year in the window, full history).
-- $artist_ids BIGINT[], $min_year INTEGER, $max_year INTEGER
WITH requested_artists AS (
    SELECT DISTINCT unnest($artist_ids) AS artist_id
)
SELECT CAST(arr.artist_id AS VARCHAR) AS artist_id,
       count(DISTINCT arr.release_id) AS n_releases
FROM requested_artists
JOIN artist_release_roles arr USING (artist_id)
JOIN releases r USING (release_id)
WHERE r.year BETWEEN $min_year AND $max_year
GROUP BY arr.artist_id
ORDER BY arr.artist_id;

-- name: label_name
-- $label_id BIGINT
SELECT coalesce(
    (SELECT name FROM labels WHERE label_id = $label_id),
    (SELECT min(label_name) FROM release_labels WHERE label_id = $label_id)
) AS name;

-- name: artist_name
-- $artist_id BIGINT
SELECT name
FROM artists
WHERE artist_id = $artist_id;

-- name: catalog_videos
-- dnx catalog of one label or one artist. Only original releases (no master, or the master's
-- main release). Releases are taken newest first and added whole while the playlist stays
-- within $max_tracks items; a release brings its videos, or its playable tracklist entries
-- when it has no videos. Playable: sub-tracks replace their index track; headings (no position
-- and no duration on a release with positions) and empty titles are dropped. Sub-tracks take
-- the parent track's artists, then the main artists.
-- $label_id, $artist_id BIGINT (one is NULL), $min_year, $max_year, $max_tracks INTEGER
WITH catalog AS (
    SELECT release_id FROM release_labels WHERE label_id = $label_id
    UNION
    SELECT release_id FROM artist_release_roles WHERE artist_id = $artist_id
),
catalog_releases AS (
    SELECT r.release_id, r.title, r.year, r.master_id
    FROM releases r
    WHERE r.release_id IN (SELECT release_id FROM catalog)
      AND (r.year IS NULL OR r.year BETWEEN coalesce($min_year, 0) AND coalesce($max_year, 9999))
      AND (r.master_id IS NULL OR r.is_main_release)
),
catalog_tracks AS (
    SELECT release_id, seq,
           coalesce(trim(position), '') AS position,
           coalesce(trim(title), '')    AS title,
           coalesce(trim(duration), '') AS duration
    FROM tracks
    WHERE release_id IN (SELECT release_id FROM catalog_releases)
),
catalog_sub_tracks AS (
    SELECT release_id, track_seq, seq,
           coalesce(trim(position), '') AS position,
           coalesce(trim(title), '')    AS title,
           coalesce(trim(duration), '') AS duration
    FROM sub_tracks
    WHERE release_id IN (SELECT release_id FROM catalog_releases)
),
index_tracks AS (
    SELECT DISTINCT release_id, track_seq FROM catalog_sub_tracks
),
positioned_releases AS (
    SELECT release_id FROM catalog_tracks GROUP BY release_id HAVING bool_or(position <> '')
),
playable_entries AS (
    -- index tracks give way to their sub-tracks; headings have no position and no duration
    SELECT t.release_id, t.seq AS track_seq, -1 AS sub_seq, t.position, t.title, t.duration
    FROM catalog_tracks t
    LEFT JOIN index_tracks i ON i.release_id = t.release_id AND i.track_seq = t.seq
    WHERE i.release_id IS NULL
      AND t.title <> ''
      AND NOT (t.position = '' AND t.duration = ''
               AND t.release_id IN (SELECT release_id FROM positioned_releases))
    UNION ALL
    SELECT release_id, track_seq, seq, position, title, duration
    FROM catalog_sub_tracks
    WHERE title <> ''
),
video_counts AS (
    SELECT release_id, count(*) AS n_videos
    FROM release_videos
    WHERE release_id IN (SELECT release_id FROM catalog_releases)
    GROUP BY release_id
),
track_counts AS (
    SELECT release_id, count(*) AS n_tracks
    FROM playable_entries
    GROUP BY release_id
),
playlist_items AS (
    SELECT c.release_id, c.title, c.year, c.master_id,
           coalesce(v.n_videos, t.n_tracks, 0) AS n_items
    FROM catalog_releases c
    LEFT JOIN video_counts v USING (release_id)
    LEFT JOIN track_counts t USING (release_id)
),
selected_releases AS (
    SELECT release_id, title, year, master_id
    FROM (
        SELECT release_id, title, year, master_id,
               sum(n_items) OVER (ORDER BY year DESC NULLS LAST, release_id DESC
                                  ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS playlist_size
        FROM playlist_items
    ) AS running_totals
    WHERE playlist_size <= $max_tracks
),
release_video_lists AS (
    SELECT release_id,
           list({'url': url, 'title': coalesce(title, '')} ORDER BY seq) AS videos
    FROM release_videos
    WHERE release_id IN (SELECT release_id FROM selected_releases)
    GROUP BY release_id
),
release_format_descriptions AS (
    SELECT release_id, flatten(list(coalesce(descriptions, []) ORDER BY seq)) AS format_descriptions
    FROM release_formats
    WHERE release_id IN (SELECT release_id FROM selected_releases)
    GROUP BY release_id
),
main_artist_names AS (
    SELECT release_id,
           string_agg(artist_name, ', ' ORDER BY seq) AS display_name,
           string_agg(artist_name, ', ' ORDER BY seq)
               FILTER (WHERE lower(trim(artist_name)) NOT IN ('various', 'various artists')) AS credited_names
    FROM release_artists
    WHERE release_id IN (SELECT release_id FROM selected_releases)
    GROUP BY release_id
),
track_artist_names AS (
    -- track_artists has no seq column; rowid (DuckDB's row number) keeps the source file's order
    SELECT release_id,
           track_seq,
           string_agg(artist_name, ', ' ORDER BY rowid) AS credited_names
    FROM track_artists
    WHERE release_id IN (SELECT release_id FROM selected_releases)
      AND lower(trim(artist_name)) NOT IN ('various', 'various artists')
    GROUP BY release_id, track_seq
),
release_tracklists AS (
    SELECT pe.release_id,
           list({'position': pe.position,
                 'title': pe.title,
                 'artist': coalesce(ta.credited_names, ma.credited_names, ''),
                 'duration': pe.duration} ORDER BY pe.track_seq, pe.sub_seq) AS tracklist
    FROM playable_entries pe
    LEFT JOIN track_artist_names ta ON ta.release_id = pe.release_id AND ta.track_seq = pe.track_seq
    LEFT JOIN main_artist_names ma ON ma.release_id = pe.release_id
    WHERE pe.release_id IN (SELECT release_id FROM selected_releases)
    GROUP BY pe.release_id
),
catalog_label_names AS (
    SELECT release_id, min(label_name) AS label_name
    FROM release_labels
    WHERE label_id = $label_id
    GROUP BY release_id
)
SELECT CAST(s.release_id AS VARCHAR)   AS release_id,
       coalesce(s.title, '')           AS release_title,
       coalesce(ma.display_name, '')   AS artist_name,
       s.year,
       CAST(s.master_id AS VARCHAR)    AS master_id,
       fd.format_descriptions,
       v.videos,
       tl.tracklist,
       coalesce(ln.label_name, '')     AS label_name
FROM selected_releases s
LEFT JOIN release_video_lists v USING (release_id)
LEFT JOIN release_tracklists tl USING (release_id)
LEFT JOIN release_format_descriptions fd USING (release_id)
LEFT JOIN main_artist_names ma USING (release_id)
LEFT JOIN catalog_label_names ln USING (release_id)
ORDER BY s.year DESC NULLS LAST, s.release_id DESC;
