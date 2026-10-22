FROM postgres:18

RUN apt-get update \
 && apt-get install -y --no-install-recommends curl unzip ca-certificates \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY load_kaggle.sql seed.sh ./
RUN chmod +x seed.sh

# Le dossier /data peut être monté pour garder les CSV entre deux lancements
VOLUME ["/data"]

ENTRYPOINT ["/app/seed.sh"]
