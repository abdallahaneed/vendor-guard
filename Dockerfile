FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY vendor_guard ./vendor_guard
RUN pip install --no-cache-dir .

ENV VENDOR_GUARD_HOME=/data
VOLUME /data
EXPOSE 8100

RUN useradd --create-home app && mkdir -p /data && chown app /data
USER app
CMD ["vendor-guard", "serve", "--host", "0.0.0.0", "--port", "8100"]
