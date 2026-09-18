#!/usr/bin/env bash
# One-shot setup on a fresh EC2 box (Amazon Linux 2023 or Ubuntu 24.04):
# system packages, a venv, the steam-zarr CLI, and the AWS CLI if missing.
set -euo pipefail
cd "$(dirname "$0")"

# libcurl dev headers matter: pyBigWig builds from source on new Pythons and
# silently drops remote-URL support if they are missing (pyBigWig.remote == 0).
if command -v dnf >/dev/null; then
  sudo dnf install -y -q python3.11 python3.11-pip git gcc zlib-devel libcurl-devel awscli >/dev/null 2>&1 || sudo dnf install -y -q python3 python3-pip git gcc zlib-devel libcurl-devel
elif command -v apt-get >/dev/null; then
  sudo apt-get update -qq && sudo apt-get install -y -qq python3 python3-venv python3-pip git build-essential zlib1g-dev libcurl4-openssl-dev awscli
fi

PY="$(command -v python3.11 || command -v python3.12 || command -v python3)"
echo "Using $($PY --version)"
[ -d .venv ] || "$PY" -m venv .venv
# shellcheck disable=SC1091
source .venv/bin/activate
pip install -q --upgrade pip
# force a source build of pyBigWig so it links against libcurl (wheels for older
# Pythons are fine; from-source builds without the headers lose remote support)
pip install -q --no-binary pyBigWig --force-reinstall --no-deps pyBigWig
pip install -q -e .
python - <<'PY'
import numpy, pyBigWig, zarr, Bio, sys
print("deps ok:", "numpy", numpy.__version__, "zarr", zarr.__version__, "pyBigWig remote:", pyBigWig.remote)
if not pyBigWig.remote:
    sys.exit("ERROR: pyBigWig has no remote-URL support — install libcurl dev headers and re-run setup.sh")
PY
command -v aws >/dev/null && aws --version || echo "note: aws CLI not found — install it for --upload (https://aws.amazon.com/cli/)"
echo
echo "Activate with:  source $(pwd)/.venv/bin/activate"
echo "Then:           steam-zarr bench --workers 48"
