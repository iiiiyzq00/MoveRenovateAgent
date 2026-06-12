FROM node:20-alpine

WORKDIR /app

COPY package.json ./
RUN npm install

COPY . .

# 构建生产版本（可选：开发模式用 vite dev）
RUN npm run build || true

EXPOSE 3000

# 开发模式（生产部署需改用 nginx）
CMD ["npx", "vite", "--host", "0.0.0.0", "--port", "3000"]
