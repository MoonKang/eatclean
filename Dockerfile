# Container for the scheduled collector (Cloud Run Job).
# The gcloud image (Debian) bundles a private Python for gcloud itself but has no
# `python3` command, so install Debian's Python for the collector scripts.
FROM gcr.io/google.com/cloudsdktool/google-cloud-cli:slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends python3 \
 && rm -rf /var/lib/apt/lists/* \
 && python3 --version

WORKDIR /app
COPY collector/ collector/
COPY web/ web/
COPY deploy/entrypoint.sh entrypoint.sh

ENTRYPOINT ["bash", "/app/entrypoint.sh"]
