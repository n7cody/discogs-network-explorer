"""
Entry point for the `dne` CLI command.

Resolves the absolute path to app.py, ensures the src/ root is on
sys.path so package-relative imports work regardless of the working
directory, then delegates to Streamlit's internal bootstrap runner.
"""

import sys
from pathlib import Path

from streamlit.web import bootstrap


def main() -> None:
    app_path = Path(__file__).resolve().with_name("app.py")

    # Add the src/ root (parent of the package directory) to sys.path
    # so that `import discogs_network_explorer.*` resolves correctly.
    src_root = str(app_path.parent.parent)
    if src_root not in sys.path:
        sys.path.insert(0, src_root)

    bootstrap.run(str(app_path), False, [], {
        "client.toolbarMode": "minimal",
        "theme.primaryColor": "#00DDFF",
        "theme.backgroundColor": "#00161A",
        "theme.secondaryBackgroundColor": "#003842",
        "theme.textColor": "#F5FFFF",
    })


if __name__ == "__main__":
    main()
