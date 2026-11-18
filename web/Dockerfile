# Build stage
FROM node:25-alpine AS build
WORKDIR /app

# Vite inlines env vars at build time
ARG VITE_SUPABASE_URL
ARG VITE_SUPABASE_ANON_KEY
ENV VITE_SUPABASE_URL=$VITE_SUPABASE_URL
ENV VITE_SUPABASE_ANON_KEY=$VITE_SUPABASE_ANON_KEY
# Short git hash shown in the sidebar foot (set by the deploy workflow).
ARG VITE_GIT_COMMIT=unknown
ENV VITE_GIT_COMMIT=$VITE_GIT_COMMIT

COPY package.json ./
RUN npm install
COPY . .
RUN npm run build

# Serve stage. Unprivileged nginx: runs as uid 101, no root in the container.
FROM nginxinc/nginx-unprivileged:1.31-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
# Writes /config.js from SUPABASE_URL_PUBLIC and SUPABASE_ANON_KEY at start.
COPY docker-entrypoint.d/10-runtime-config.sh /docker-entrypoint.d/10-runtime-config.sh
USER root
RUN chmod +x /docker-entrypoint.d/10-runtime-config.sh \
    && chown -R nginx:nginx /usr/share/nginx/html
USER nginx
EXPOSE 3000
CMD ["nginx", "-g", "daemon off;"]
