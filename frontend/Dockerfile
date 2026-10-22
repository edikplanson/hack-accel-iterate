FROM nginx:1.27-alpine

# PORT : port d'écoute du front. AGENT_URL : adresse de l'agent (nom du service en local,
# URL publique de l'app agent sur Aiven Runtime si les deux apps ne partagent pas de réseau)
ENV PORT=8080 \
    AGENT_URL=http://agent:8000 \
    NGINX_ENVSUBST_FILTER="^(PORT|AGENT_URL)$"

COPY nginx.conf.template /etc/nginx/templates/default.conf.template
COPY public /usr/share/nginx/html

EXPOSE 8080
