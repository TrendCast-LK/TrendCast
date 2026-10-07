# TrendCast web app: built with Vite, served by nginx, which also forwards
# /api/* to the backend container (see nginx.conf), so the browser only ever
# talks to one origin and no CORS setup is needed.
FROM node:22-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
# Baked in at build time (Vite inlines import.meta.env).
ARG VITE_API_URL=/api
ARG VITE_ENSEMBLE_SINCE=
ENV VITE_API_URL=$VITE_API_URL \
    VITE_ENSEMBLE_SINCE=$VITE_ENSEMBLE_SINCE
RUN npm run build

FROM nginx:1.27-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
