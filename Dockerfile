# The sre-agent webhook server, for the demo environment.
FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir ".[server]"
EXPOSE 8080
CMD ["uvicorn", "sre_agent.server:app", "--host", "0.0.0.0", "--port", "8080"]
