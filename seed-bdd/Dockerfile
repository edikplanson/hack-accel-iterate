FROM postgres:18

RUN apt-get update \
 && apt-get install -y --no-install-recommends curl unzip ca-certificates python3-minimal \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY load_kaggle.sql seed.sh ./
RUN chmod +x seed.sh

ENV PORT=8080
EXPOSE 8080

ENTRYPOINT ["/app/seed.sh"]
