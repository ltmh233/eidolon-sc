FROM golang:1.27-alpine AS build
WORKDIR /src
COPY go.mod ./
COPY cmd ./cmd
COPY internal ./internal
RUN CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /eidolon-core ./cmd/eidolon-core

FROM scratch
COPY --from=build /eidolon-core /eidolon-core
COPY packs /packs
USER 65532:65532
ENTRYPOINT ["/eidolon-core"]
CMD ["-listen", "0.0.0.0:8081", "-packs", "/packs", "-legacy", "http://backend:8000"]
