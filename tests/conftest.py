import gzip
import hashlib
import shutil
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"
DB_DATE = "20261001"
KINDS = ("labels", "artists", "masters", "releases")


@pytest.fixture(scope="session")
def source_dir(tmp_path_factory):
    """Fixture XML packaged like a real database release: gzipped files plus CHECKSUM.txt."""
    d = tmp_path_factory.mktemp("source")
    lines = []
    for kind in KINDS:
        name = f"discogs_{DB_DATE}_{kind}.xml.gz"
        with open(FIXTURES / f"{kind}_sample.xml", "rb") as src, gzip.open(d / name, "wb") as dst:
            shutil.copyfileobj(src, dst)
        lines.append(f"{hashlib.sha256((d / name).read_bytes()).hexdigest()} {name}")
    (d / f"discogs_{DB_DATE}_CHECKSUM.txt").write_text("\n".join(lines) + "\n")
    return d


def _stage(source_dir, out, variant, cores=1):
    from discogs_network_explorer.ingest.parse import IngestOptions, parse_xml

    opts = IngestOptions(variant, cores=cores)
    for kind in opts.kinds:
        with gzip.open(source_dir / f"discogs_{DB_DATE}_{kind}.xml.gz", "rb") as f:
            parse_xml(kind, f, out, opts=opts)
    return out


def _build(staging, db, variant):
    from discogs_network_explorer.ingest.build import build_snapshot
    from discogs_network_explorer.ingest.parse import IngestOptions

    build_snapshot(staging, db, db_date=DB_DATE, checksums={"x": "y"}, timings={"parse": 1.0},
                   opts=IngestOptions(variant))
    return db


@pytest.fixture
def spawn_safe_main(monkeypatch):
    """Spawned workers re-run the parent's __main__; AppTest leaves app.py there."""
    import sys
    import types

    monkeypatch.setitem(sys.modules, "__main__", types.ModuleType("__main__"))


@pytest.fixture(scope="session")
def staging(source_dir, tmp_path_factory):
    """The fixture parsed as the full variant."""
    return _stage(source_dir, tmp_path_factory.mktemp("staging"), "full")


@pytest.fixture(scope="session")
def staging_trimmed(source_dir, tmp_path_factory):
    return _stage(source_dir, tmp_path_factory.mktemp("staging_trimmed"), "trimmed")


@pytest.fixture(scope="session")
def snapshot(staging, tmp_path_factory):
    """The fixture database, full variant."""
    return _build(staging, tmp_path_factory.mktemp("db") / "snap.duckdb", "full")


@pytest.fixture(scope="session")
def snapshot_trimmed(staging_trimmed, tmp_path_factory):
    return _build(staging_trimmed, tmp_path_factory.mktemp("db_trimmed") / "snap.duckdb", "trimmed")


@pytest.fixture(scope="session")
def download_server(source_dir):
    """Local stand-in for data.discogs.com serving `source_dir` as year 2026."""
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from urllib.parse import parse_qs, urlparse

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            q = parse_qs(urlparse(self.path).query)
            if q.get("prefix") == ["data/2026/"]:
                links = "".join(f'<a href="?download=data%2F2026%2F{p.name}">{p.name}</a>\n'
                                for p in sorted(source_dir.iterdir()))
                body = f'<a href="?prefix=data%2F">../</a>\n{links}'.encode()
            elif q.get("prefix"):
                body = b"<pre></pre>"
            elif q.get("download", [""])[0].startswith("data/2026/"):
                p = source_dir / q["download"][0].removeprefix("data/2026/")
                if not p.is_file():
                    self.send_error(404)
                    return
                body = p.read_bytes()
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}/"
    server.shutdown()
