ARG BUILD_FROM
FROM $BUILD_FROM

RUN apk add --no-cache python3
WORKDIR /app
COPY app.py metadata.py /app/
COPY web /app/web
CMD ["/usr/bin/with-contenv", "python3", "/app/app.py"]
