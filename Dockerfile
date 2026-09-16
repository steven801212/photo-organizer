FROM python:3.12-slim
LABEL org.opencontainers.image.title="Photo Organizer" org.opencontainers.image.version="1.0.1"
RUN apt-get update && apt-get install -y --no-install-recommends libimage-exiftool-perl gosu && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
COPY docker-entrypoint.sh /usr/local/bin/photo-organizer-entrypoint
RUN chmod 755 /usr/local/bin/photo-organizer-entrypoint
RUN pip install --no-cache-dir .
EXPOSE 8080
ENTRYPOINT ["photo-organizer-entrypoint"]
CMD ["python", "-m", "photo_organizer.service"]
