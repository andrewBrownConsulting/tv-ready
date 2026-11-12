ARG BUILD_FROM
FROM ${BUILD_FROM}

RUN apk add --no-cache python3 ffmpeg \
 && (apk add --no-cache intel-media-driver libva-intel-driver || true)   # Intel Quick Sync drivers (amd64 only)

COPY app /app
WORKDIR /app

CMD ["python3", "-u", "/app/tvready.py"]
