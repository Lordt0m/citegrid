#!/usr/bin/env bash
# Prepare the public CiteGrid example on LordTom's PythonAnywhere Beginner account.
set -euo pipefail

project_dir="/home/LordTom/citegrid"
venv_dir="/home/LordTom/.virtualenvs/citegrid"

if [[ "$HOME" != "/home/LordTom" || ! -f "$project_dir/manage.py" ]]; then
    echo "Run this from the cloned CiteGrid repository in the LordTom PythonAnywhere account." >&2
    exit 1
fi

python3.13 -m venv "$venv_dir"
"$venv_dir/bin/python" -m pip install --no-cache-dir -r "$project_dir/requirements.txt"

cd "$project_dir"
if [[ ! -e .env ]]; then
    umask 077
    secret_key=$("$venv_dir/bin/python" -c 'from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())')
    printf 'DEBUG=False\nSECRET_KEY=%s\nALLOWED_HOSTS=lordtom.pythonanywhere.com\nDATABASE_URL=sqlite:////home/LordTom/citegrid/demo.sqlite3\nCITEGRID_IS_DEMO_DB=True\n' "$secret_key" > .env
    unset secret_key
else
    echo "Using the existing .env file; check its host, database, and demo settings before continuing."
fi

"$venv_dir/bin/python" manage.py setup_demo --allow-simulation
"$venv_dir/bin/python" manage.py collectstatic --noinput
"$venv_dir/bin/python" manage.py check

echo "CiteGrid is prepared. Set the web app virtualenv to $venv_dir, configure its WSGI file and /static/ mapping, then reload."
