# 前端镜像：一个 Nginx 同时托管聊天端（/）与管理后台（/admin/）
FROM node:20-alpine AS build

WORKDIR /src

# 先只复制清单文件，依赖未变时复用 npm ci 层缓存（npm workspaces 需要各子包 package.json）
COPY package.json package-lock.json ./
COPY packages/shared/package.json packages/shared/
COPY frontend/package.json frontend/
COPY admin-frontend/package.json admin-frontend/
RUN npm ci

COPY packages/shared packages/shared
COPY frontend frontend
COPY admin-frontend admin-frontend

RUN npm run build -w frontend
# 管理后台挂在 /admin/ 下，Vite base 由 VITE_BASE 注入
RUN VITE_BASE=/admin/ npm run build -w admin-frontend

# nginx ≥1.27.3 才开源支持 upstream server 的 resolve 参数（见 docker/nginx.conf）
FROM nginx:1.27.5-alpine

COPY docker/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /src/frontend/dist /usr/share/nginx/html
COPY --from=build /src/admin-frontend/dist /usr/share/nginx/html/admin

EXPOSE 80
