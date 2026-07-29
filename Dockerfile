FROM node:22-alpine AS builder
WORKDIR /app
COPY package.json yarn.lock ./
COPY dashboard/package.json ./dashboard/
RUN corepack enable && yarn install --frozen-lockfile
COPY . .
RUN yarn build

FROM nginx:alpine AS runner
COPY --from=builder /app/dashboard/dist /usr/share/nginx/html
COPY dashboard/nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
