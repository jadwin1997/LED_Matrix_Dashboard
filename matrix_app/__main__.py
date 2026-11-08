"""Start output before any browser connects, then serve the shared LAN UI."""

from pathlib import Path
import sys

from streamlit.web import cli

from .worker import get_worker


def main():
    worker = get_worker()
    try:
        cli.main(args=["run", str(Path(__file__).resolve().parents[1] / "app.py"), *sys.argv[1:]],
                 prog_name="streamlit")
    finally:
        worker.close()


if __name__ == "__main__":
    main()
