FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY redthread ./redthread

RUN useradd --create-home redthread && mkdir /data && chown redthread /data
USER redthread

ENV REDTHREAD_DATA=/data HOST=0.0.0.0 PORT=8080 PYTHONUNBUFFERED=1
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request as u; u.urlopen('http://127.0.0.1:8080/healthz', timeout=3)"

CMD ["python", "-m", "redthread.server"]
