FROM busybox:1.37.0-musl AS busybox
FROM grafana/loki:3.7.9

# distroless image has no wget; add a static busybox for the healthcheck
COPY --from=busybox /bin/busybox /usr/bin/wget
