FROM node:20-alpine
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --omit=dev
COPY src ./src
COPY public ./public
COPY migrations ./migrations
ENV NODE_ENV=production
EXPOSE 3101
CMD ["npm", "start"]
