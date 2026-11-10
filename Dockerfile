# The sre-agent webhook server, for the demo environment.
FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml README.md requirements.lock ./
COPY src ./src
# Dependencies come from the lock file, with hashes checked
# (requirements.lock; finding F-06). The package itself is then
# installed without dependencies, because the lock already
# installed the exact ones it needs.
RUN pip install --no-cache-dir --require-hashes -r requirements.lock \
    && pip install --no-cache-dir --no-deps .
# The server does not run as root (finding F-06).
RUN useradd --create-home --uid 10001 sreagent \
    && chown -R sreagent:sreagent /app
USER sreagent
EXPOSE 8080
CMD ["uvicorn", "sre_agent.server:app", "--host", "0.0.0.0", "--port", "8080"]
