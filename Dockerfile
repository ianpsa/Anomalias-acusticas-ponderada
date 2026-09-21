# syntax=docker/dockerfile:1
FROM python:3.13-slim-bookworm AS base
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1 \
    HOME=/tmp OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends gcc libc6-dev libgomp1 \
    && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml ./
COPY ferris/ ferris/
COPY tools/ tools/
COPY firmware/ firmware/
COPY docker/ docker/
RUN mkdir -p data models build && chown -R 1000:1000 /app

FROM base AS ferris
RUN pip install '.[train,device]' && python tools/training/build_dsp.py \
    && chmod 777 build
USER 1000:1000
ENTRYPOINT ["sh", "/app/docker/entrypoint.sh"]
CMD ["ferris"]

FROM ferris AS test
COPY tests/ tests/
CMD ["python", "-m", "unittest", "discover", "-s", "tests", "-v"]

FROM base AS voice
RUN pip install '.[voice,tts]' 'numpy>=1.26,<3' 'onnxruntime>=1.18,<2'
USER 1000:1000
ENTRYPOINT ["sh", "/app/docker/entrypoint.sh"]
CMD ["voice"]
