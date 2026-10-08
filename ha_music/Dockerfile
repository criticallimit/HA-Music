ARG BUILD_FROM
FROM $BUILD_FROM

RUN apk add --no-cache python3
WORKDIR /app
COPY app.py /app/app.py
COPY web /app/web
CMD ["python3", "/app/app.py"]
