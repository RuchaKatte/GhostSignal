FROM python:3.11-slim
WORKDIR /app
RUN pip install --no-cache-dir httpx structlog asyncio
COPY loadgen.py .
CMD ["python", "loadgen.py"]
