#############################
# Stage 1: Builder
#############################
FROM python:3.12-slim-bookworm AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY nextcloud/requirements.txt /requirements.txt
RUN --mount=type=cache,target=/root/.cache/pip \
    python3 -m pip install --root-user-action=ignore -r /requirements.txt

#############################
# Stage 2: Runtime
#############################
FROM python:3.12-slim-bookworm

COPY --from=builder /usr/local/ /usr/local/

RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg \
        libsndfile1 \
        curl \
        procps \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /ex_app
COPY nextcloud/ex_app ./ex_app
COPY static ./static
COPY --chmod=775 nextcloud/start.sh /start.sh

WORKDIR /ex_app/lib
ENV ACOUSTIC_STATIC_DIR=/ex_app/static
ENTRYPOINT ["/start.sh", "python3", "main.py"]
