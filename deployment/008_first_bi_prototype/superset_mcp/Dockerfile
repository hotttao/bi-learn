# Native MCP service shipped with the already tested Superset 6.1.0 image.
FROM bi008-superset:6.1.0
USER root
ARG PYPI_INDEX_URL=https://mirrors.tuna.tsinghua.edu.cn/pypi/web/simple
COPY freeze_constraints.py /tmp/freeze_constraints.py
COPY requirements.lock /tmp/bi008-mcp.lock
RUN /app/.venv/bin/python /tmp/freeze_constraints.py > /tmp/bi008-base.txt \
    && uv pip install --python /app/.venv/bin/python --index-url "${PYPI_INDEX_URL}" \
       --constraint /tmp/bi008-base.txt --requirement /tmp/bi008-mcp.lock \
    && uv pip check --python /app/.venv/bin/python
USER superset
