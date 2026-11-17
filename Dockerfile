# Container for the scheduled collector (Cloud Run Job).
# The gcloud image already has Python 3, which is all the collector needs.
FROM gcr.io/google.com/cloudsdktool/google-cloud-cli:slim

WORKDIR /app
COPY collector/ collector/
COPY web/ web/
COPY deploy/entrypoint.sh entrypoint.sh

ENTRYPOINT ["bash", "/app/entrypoint.sh"]
