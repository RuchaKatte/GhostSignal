FROM python:3.11-slim
WORKDIR /app
RUN pip install --no-cache-dir httpx pyyaml click structlog
COPY injector.py .
COPY scenarios/ scenarios/
CMD ["tail", "-f", "/dev/null"]
