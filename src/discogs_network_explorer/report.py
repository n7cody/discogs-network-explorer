"""
report.py — Export utilities for generating shareable analysis reports.

Provides two export formats:

  HTML report — standalone file with embedded graph PNG (base64), input
                parameters, artist pools, result table, and metadata.

  ZIP archive — bundle containing the HTML report, a CSV of all result
                rows, and the graph as a separate PNG file.
"""

from __future__ import annotations

import io
import zipfile
from datetime import datetime, timezone

import pandas as pd
from matplotlib.figure import Figure


def generate_report_html(
    seed_label_ids: list[str],
    seed_artist_ids: list[str],
    master_artist_pools: object,
    df_results: pd.DataFrame,
    graph_fig: Figure,
    metadata: dict,
) -> str:
    """
    Build a standalone HTML report string.

    The graph is embedded as a base64-encoded PNG so the file is fully
    self-contained and can be shared without external dependencies.

    Args:
        seed_label_ids:      Label IDs used as seeds.
        seed_artist_ids:     Artist IDs used as seeds (may be empty).
        master_artist_pools: Artist pool data (any repr-able object).
        df_results:          Filtered result DataFrame.
        graph_fig:           Matplotlib Figure to embed.
        metadata:            Arbitrary key-value dict of run parameters.

    Returns:
        HTML string.
    """
    import base64

    buf = io.BytesIO()
    graph_fig.savefig(buf, format="png", bbox_inches="tight")
    graph_b64 = base64.b64encode(buf.getvalue()).decode()
    buf.close()

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>Discogs Network Explorer Report</title>
  <style>
    body  {{ font-family: Arial, sans-serif; padding: 24px; max-width: 1100px; margin: auto; }}
    h1    {{ border-bottom: 2px solid #333; padding-bottom: 8px; }}
    h2    {{ margin-top: 32px; color: #444; }}
    pre   {{ background: #f5f5f5; padding: 12px; border-radius: 6px;
             overflow-x: auto; font-size: 13px; }}
    img   {{ max-width: 100%; border: 1px solid #ddd; border-radius: 4px; }}
    table {{ border-collapse: collapse; width: 100%; font-size: 13px; }}
    th, td {{ border: 1px solid #ccc; padding: 6px 10px; text-align: left; }}
    th    {{ background: #eee; }}
  </style>
</head>
<body>

  <h1>Discogs Network Explorer Report</h1>
  <p>Generated: {timestamp}</p>

  <h2>Inputs</h2>
  <pre>Seed Labels:  {seed_label_ids}
Seed Artists: {seed_artist_ids}</pre>

  <h2>Master Artist Pools</h2>
  <pre>{master_artist_pools}</pre>

  <h2>Results — first 50 rows</h2>
  {df_results.head(50).to_html(index=False, border=0)}

  <h2>Network Graph</h2>
  <img src="data:image/png;base64,{graph_b64}" alt="Network graph" />

  <h2>Run Metadata</h2>
  <pre>{metadata}</pre>

</body>
</html>"""

    return html


def generate_report_zip(
    html_report: str,
    df_results: pd.DataFrame,
    graph_fig: Figure,
) -> bytes:
    """
    Build an in-memory ZIP archive containing the full report bundle.

    Archive contents:
        report.html  — standalone HTML report (see generate_report_html)
        results.csv  — all result rows as CSV
        graph.png    — network graph as a PNG image

    Returns:
        Raw bytes of the ZIP file, suitable for st.download_button.
    """
    buf = io.BytesIO()

    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("report.html", html_report)
        z.writestr("results.csv", df_results.to_csv(index=False))

        fig_buf = io.BytesIO()
        graph_fig.savefig(fig_buf, format="png", bbox_inches="tight")
        z.writestr("graph.png", fig_buf.getvalue())
        fig_buf.close()

    buf.seek(0)
    return buf.getvalue()
