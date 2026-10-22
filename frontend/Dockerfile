FROM nginx:1.27-alpine

# PORT: port the frontend listens on. AGENT_URL: agent address (service name locally,
# public URL of the agent app on Aiven Runtime if the two apps do not share a network)
ENV PORT=8080 \
    AGENT_URL=http://agent:8000 \
    NGINX_ENTRYPOINT_LOCAL_RESOLVERS=1 \
    NGINX_ENVSUBST_FILTER="^(PORT|AGENT_URL|NGINX_LOCAL_RESOLVERS)$"

COPY nginx.conf.template /etc/nginx/templates/default.conf.template
COPY public /usr/share/nginx/html

EXPOSE 8080
