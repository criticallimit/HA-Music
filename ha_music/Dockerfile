ARG BUILD_ARCH
FROM ghcr.io/home-assistant/${BUILD_ARCH}-base:3.21

RUN apk add --no-cache python3
WORKDIR /app
COPY app.py metadata.py metadata_feed.py logo_sources.py /app/
COPY web /app/web
RUN python3 /app/logo_sources.py
CMD ["/usr/bin/with-contenv", "python3", "/app/app.py"]
