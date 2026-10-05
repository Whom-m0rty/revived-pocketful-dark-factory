FROM golang:1.24-alpine AS build
WORKDIR /src
COPY go.mod ./
COPY *.go ./
COPY web ./web
RUN CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /pocketful .

FROM alpine:3.20
COPY --from=build /pocketful /usr/local/bin/pocketful
ENV PORT=8080
EXPOSE 8080
USER nobody
CMD ["/usr/local/bin/pocketful"]
