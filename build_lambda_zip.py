"""
Build a Lambda deployment zip for the "zip + AWS-managed pandas layer" path.

This produces `lambda_deploy.zip` containing:
  * the application source (nb_insights.py, pull_zip_state_federal.py)
  * the dependencies that are NOT in the AWS-managed pandas layer, installed as
    Linux (manylinux) wheels so they run on the Lambda runtime.

pandas and numpy are intentionally EXCLUDED — they come from the AWS-managed
layer `AWSSDKPandas-Python312` attached to the function (see DEPLOY.md). boto3
is also excluded because it is already in the Lambda runtime.

Why a script (not `pip install` into a folder directly): on Windows, a plain
`pip install` grabs Windows wheels that will not run on Lambda. We force Linux
wheels with `--platform manylinux2014_x86_64 --only-binary=:all:`.

Usage (from the repo root, any OS with Python + pip):
    python build_lambda_zip.py
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BUILD_DIR = ROOT / "build" / "package"
ZIP_PATH = ROOT / "lambda_deploy.zip"

# Application source files to include at the zip root.
SOURCE_FILES = ["nb_insights.py", "pull_zip_state_federal.py"]

# Dependencies to bundle. pandas/numpy (managed layer) and boto3 (runtime) are
# deliberately omitted. These are the pins from requirements.txt minus those.
DEPENDENCIES = [
    "tableau-api-lib==0.1.45",
    "openpyxl==3.1.5",
    "python-dotenv==1.0.1",
]

# Packages provided by the environment, so they must NOT be in the zip:
#   pandas, numpy  -> AWS-managed pandas layer (AWSSDKPandas-Python312)
#   boto3, botocore, s3transfer, jmespath, dateutil, six, urllib3 -> also in
#     that layer / the Lambda runtime.
# tableau-api-lib lists pandas as a dependency, so pip installs it transitively;
# we prune these top-level dirs after install to avoid shipping (and shadowing)
# the layer's copies. Pruning by import-name prefix keeps it simple and safe.
PROVIDED_BY_LAYER = ["pandas", "numpy"]

# Lambda x86_64 / Python 3.12 wheel target.
PIP_PLATFORM = "manylinux2014_x86_64"
PYTHON_VERSION = "3.12"


def clean() -> None:
    if BUILD_DIR.exists():
        shutil.rmtree(BUILD_DIR.parent)
    BUILD_DIR.mkdir(parents=True, exist_ok=True)
    if ZIP_PATH.exists():
        ZIP_PATH.unlink()


def pip_install_linux_wheels() -> None:
    """Install dependencies as Linux wheels into BUILD_DIR."""
    cmd = [
        sys.executable, "-m", "pip", "install",
        "--target", str(BUILD_DIR),
        "--platform", PIP_PLATFORM,
        "--python-version", PYTHON_VERSION,
        "--implementation", "cp",
        "--only-binary=:all:",
        "--upgrade",
        *DEPENDENCIES,
    ]
    print("Running:", " ".join(cmd))
    subprocess.run(cmd, check=True)


def prune_layer_packages() -> None:
    """Delete packages provided by the AWS-managed layer from BUILD_DIR.

    tableau-api-lib depends on pandas (which pulls numpy), so pip installs them
    transitively. Shipping them would bloat the zip and shadow the layer, so we
    remove their package dirs and *.dist-info / *.libs folders here.
    """
    removed = []
    for name in PROVIDED_BY_LAYER:
        for entry in BUILD_DIR.iterdir():
            base = entry.name.lower()
            # e.g. "pandas", "pandas-2.3.2.dist-info", "numpy.libs"
            if base == name or base.startswith(f"{name}-") or base.startswith(
                f"{name}."
            ):
                if entry.is_dir():
                    shutil.rmtree(entry)
                else:
                    entry.unlink()
                removed.append(entry.name)
    print("Pruned layer-provided packages:", ", ".join(sorted(removed)) or "(none)")


def build_zip() -> None:
    """Zip the installed dependencies plus the application source files."""
    with zipfile.ZipFile(ZIP_PATH, "w", zipfile.ZIP_DEFLATED) as zf:
        # Dependencies (contents of BUILD_DIR at the zip root).
        for path in BUILD_DIR.rglob("*"):
            if "__pycache__" in path.parts or path.suffix == ".pyc":
                continue
            if path.is_file():
                zf.write(path, path.relative_to(BUILD_DIR))
        # Application source at the zip root.
        for name in SOURCE_FILES:
            src = ROOT / name
            if not src.exists():
                raise FileNotFoundError(f"Missing source file: {src}")
            zf.write(src, name)
    print(f"Wrote {ZIP_PATH}  ({ZIP_PATH.stat().st_size / 1_048_576:.1f} MB)")


def main() -> None:
    clean()
    pip_install_linux_wheels()
    prune_layer_packages()
    build_zip()
    print("Done. Upload lambda_deploy.zip and attach the AWSSDKPandas layer.")


if __name__ == "__main__":
    main()
