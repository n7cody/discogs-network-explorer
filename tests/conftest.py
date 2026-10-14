import gzip
import hashlib
import shutil
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"
DUMP_DATE = "20261001"
KINDS = ("labels", "artists", "masters", "releases")


@pytest.fixture(scope="session")
def dump_dir(tmp_path_factory):
    """Fixture XML packaged like a real Dump: gzipped files plus CHECKSUM.txt."""
    d = tmp_path_factory.mktemp("dump")
    lines = []
    for kind in KINDS:
        name = f"discogs_{DUMP_DATE}_{kind}.xml.gz"
        with open(FIXTURES / f"{kind}_sample.xml", "rb") as src, gzip.open(d / name, "wb") as dst:
            shutil.copyfileobj(src, dst)
        lines.append(f"{hashlib.sha256((d / name).read_bytes()).hexdigest()} {name}")
    (d / f"discogs_{DUMP_DATE}_CHECKSUM.txt").write_text("\n".join(lines) + "\n")
    return d


@pytest.fixture(scope="session")
def staging(dump_dir, tmp_path_factory):
    from discogs_network_explorer.ingest.parse import parse_dump

    out = tmp_path_factory.mktemp("staging")
    for kind in KINDS:
        with gzip.open(dump_dir / f"discogs_{DUMP_DATE}_{kind}.xml.gz", "rb") as f:
            parse_dump(kind, f, out)
    return out


@pytest.fixture(scope="session")
def snapshot(staging, tmp_path_factory):
    from discogs_network_explorer.ingest.build import build_snapshot

    db = tmp_path_factory.mktemp("db") / "snap.duckdb"
    build_snapshot(staging, db, dump_date=DUMP_DATE, checksums={"x": "y"}, timings={"parse": 1.0})
    return db


@pytest.fixture(scope="session")
def dump_server(dump_dir):
    """Local stand-in for data.discogs.com serving `dump_dir` as year 2026."""
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
                                for p in sorted(dump_dir.iterdir()))
                body = f'<a href="?prefix=data%2F">../</a>\n{links}'.encode()
            elif q.get("prefix"):
                body = b"<pre></pre>"
            elif q.get("download", [""])[0].startswith("data/2026/"):
                p = dump_dir / q["download"][0].removeprefix("data/2026/")
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
