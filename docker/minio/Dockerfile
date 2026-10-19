FROM golang:1.24 AS build

RUN go install github.com/minio/minio@latest \
    && go install github.com/minio/mc@latest

FROM debian:bookworm-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*

COPY --from=build /go/bin/minio /usr/local/bin/minio
COPY --from=build /go/bin/mc /usr/local/bin/mc

EXPOSE 9000 9001
ENTRYPOINT ["minio"]
