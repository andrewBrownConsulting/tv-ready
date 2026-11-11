ARG BUILD_FROM
FROM ${BUILD_FROM}

RUN apk add --no-cache python3 ffmpeg

COPY app /app
WORKDIR /app

CMD ["python3", "-u", "/app/tvready.py"]
