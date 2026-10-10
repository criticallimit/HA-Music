ARG BUILD_ARCH
FROM ghcr.io/home-assistant/${BUILD_ARCH}-base:3.21

RUN apk add --no-cache python3
WORKDIR /app
COPY app.py metadata.py metadata_feed.py logo_sources.py music_assistant_sync.py /app/
COPY web /app/web
COPY lovelace /app/lovelace
COPY run.sh /run.sh
RUN chmod +x /run.sh
RUN python3 /app/logo_sources.py
CMD ["/run.sh"]
