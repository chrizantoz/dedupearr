FROM python:3.15-slim
RUN apt-get update && apt-get upgrade -y --no-install-recommends && apt-get install -y --no-install-recommends ca-certificates tzdata && rm -rf /var/lib/apt/lists/* \
 && pip uninstall -y pip && rm -rf /usr/local/lib/python3*/site-packages/pip* /root/.cache
LABEL org.opencontainers.image.source="https://github.com/chrizantoz/dedupearr" org.opencontainers.image.description="Dedupearr: the truth about your storage. Hardlinks, second copies, leftovers and orphans explained per file." org.opencontainers.image.licenses="GPL-3.0"
WORKDIR /app
COPY dedupearr.py ui.html /app/
COPY tests /app/tests
ARG VERSION=dev
ARG REVISION=
ENV PYTHONUNBUFFERED=1 APP_VERSION=$VERSION APP_REVISION=$REVISION CONFIG_DIR=/config SCAN_ROOTS=/data
EXPOSE 3092
HEALTHCHECK --interval=5m --timeout=10s --start-period=2m CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:3092/health').status==200 else 1)"]
CMD ["python", "/app/dedupearr.py", "--daemon"]
