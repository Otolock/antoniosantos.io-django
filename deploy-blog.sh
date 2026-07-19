#!/usr/bin/env bash
set -euo pipefail

cd /home/antonio/antoniosantos.io-django

git pull

source .venv/bin/activate

pip install -r requirements.txt

deno install

deno task tailwind:build

python manage.py migrate
python manage.py collectstatic --noinput

sudo systemctl restart django-blog
