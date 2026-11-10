# The demo sample service: standard library only, nothing to install.
FROM python:3.12-slim
WORKDIR /app
COPY app.py .
# The service does not run as root (finding F-06). Port 8000 is
# above 1024, so a non-root user can still listen on it.
RUN useradd --create-home --uid 10001 sample \
    && chown -R sample:sample /app
USER sample
EXPOSE 8000
CMD ["python", "app.py"]
