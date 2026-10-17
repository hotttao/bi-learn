# Superset 6.1.0's default image is lean; explicitly add the metadata driver.
# Superset 6.1's MySQL type introspection also imports MySQLdb (mysqlclient).
FROM apache/superset:6.1.0
USER root
ARG DEBIAN_MIRROR=http://deb.debian.org
RUN sed -i "s|http://deb.debian.org|${DEBIAN_MIRROR}|g" /etc/apt/sources.list.d/debian.sources
RUN /app/docker/apt-install.sh build-essential pkg-config default-libmysqlclient-dev
RUN uv pip install --python /app/.venv/bin/python psycopg2-binary==2.9.10 'PyMySQL[rsa]==1.1.1' mysqlclient==2.2.7
USER superset
