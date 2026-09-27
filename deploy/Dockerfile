# Control-plane image only. GPU inference environments are configured separately.
FROM python:3.12-slim-bookworm
WORKDIR /opt/zhilume
ARG WORKER_WHEEL=zhilume_worker-0.10.0-py3-none-any.whl
COPY dist/${WORKER_WHEEL} /tmp/wheels/
RUN pip install --no-cache-dir /tmp/wheels/*.whl && rm -rf /tmp/wheels \
    && useradd --uid 10001 --create-home zhilume \
    && mkdir -p /var/lib/zhilume-worker /var/lib/zhilume-gpu-locks \
    && chown -R zhilume:zhilume /var/lib/zhilume-worker /var/lib/zhilume-gpu-locks
USER zhilume
ENV ZHILUME_RESOURCE_LOCK_DIR=/var/lib/zhilume-gpu-locks
EXPOSE 4320
STOPSIGNAL SIGTERM
CMD ["zhilume-worker", "--state", "/var/lib/zhilume-worker", "--host", "0.0.0.0", "--port", "4320"]
