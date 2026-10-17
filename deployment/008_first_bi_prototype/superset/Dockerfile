# Superset 6.1.0's default image is lean; explicitly add the metadata driver.
# Business database drivers will be added and tested with S2/S3.
FROM apache/superset:6.1.0
USER root
RUN uv pip install --python /app/.venv/bin/python psycopg2-binary==2.9.10
USER superset
