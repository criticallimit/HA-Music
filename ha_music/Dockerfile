ARG BUILD_FROM
FROM $BUILD_FROM

RUN apk add --no-cache python3
WORKDIR /app
COPY app.py metadata.py metadata_feed.py logo_sources.py /app/
COPY web /app/web
RUN python3 /app/logo_sources.py
CMD ["/usr/bin/with-contenv", "python3", "/app/app.py"]
