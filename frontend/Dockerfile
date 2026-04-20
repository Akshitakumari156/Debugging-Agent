# =============================================================================
# frontend/Dockerfile — Nginx Server for React App
# =============================================================================

# Build stage
FROM node:18-alpine AS builder

WORKDIR /app
COPY package*.json ./
RUN npm install

COPY . .
# Expect VITE_API_URL and VITE_WS_URL to be passed via build args or environment when using docker-compose
RUN npm run build

# Serve stage
FROM nginx:alpine

# Copy custom nginx config for React Router and WebSockets
RUN echo 'server { \
    listen 80; \
    server_name localhost; \
    root /usr/share/nginx/html; \
    index index.html; \
    location / { \
        try_files $uri $uri/ /index.html; \
    } \
}' > /etc/nginx/conf.d/default.conf

COPY --from=builder /app/dist /usr/share/nginx/html

EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
