#!/bin/sh
set -eu

case "${1:-web}" in
  web)
    BOTICA_DB_ROLE=migration python manage.py migrate --noinput
    python manage.py collectstatic --noinput
    exec gunicorn botica.wsgi:application \
        --bind 0.0.0.0:8000 \
        --workers "${GUNICORN_WORKERS:-3}" \
        --limit-request-line 16384 \
        --access-logfile - \
        --error-logfile - \
        --logger-class botica.access_log.RedactingLogger
    ;;
  worker)
    until python manage.py procrastinate healthchecks >/dev/null 2>&1; do
      echo "worker: waiting for the queue schema"
      sleep 2
    done
    exec python manage.py procrastinate worker \
        --concurrency "${BOTICA_WORKER_CONCURRENCY:-1}"
    ;;
  *)
    exec "$@"
    ;;
esac
