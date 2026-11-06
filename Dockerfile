FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.lock .
RUN pip install --no-cache-dir -r requirements.lock
RUN useradd --create-home --uid 10001 scribelet && mkdir /data && chown scribelet:scribelet /data
COPY app.py processing.py bilingual.py languages.py backup.py ./
COPY static ./static
USER scribelet
ENV SCRIBELET_DATA=/data PYTHONUNBUFFERED=1
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s CMD python -c "import os,urllib.request; from urllib.parse import urlsplit; r=urllib.request.Request('http://127.0.0.1:8000/api/health',headers={'Host':urlsplit(os.environ['SCRIBELET_ORIGIN']).netloc}); urllib.request.urlopen(r,timeout=3)"
CMD ["uvicorn", "app:from_env", "--factory", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--limit-concurrency", "30", "--timeout-keep-alive", "5", "--no-access-log"]
