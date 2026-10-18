FROM alpine:3.20

# ponytail: download binary amd64 statis. Upgrade jika arsitektur ARM/arm64.
RUN apk add --no-cache bash python3 py3-cryptography ffmpeg curl \
    && curl -fSL https://github.com/bluenviron/mediamtx/releases/download/v1.11.3/mediamtx_v1.11.3_linux_amd64.tar.gz | tar -xz -C /usr/local/bin mediamtx

WORKDIR /app
# ponytail: script & config di-load via volumes compose.yaml. Tambah COPY jika butuh image standalone tanpa mount.

CMD ["mediamtx", "/app/mediamtx.yml"]
