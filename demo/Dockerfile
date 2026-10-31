FROM node:20-alpine
WORKDIR /app
COPY package.json ./
COPY src ./src
COPY public ./public
COPY test ./test
ENV NODE_ENV=production
EXPOSE 3101
CMD ["npm", "start"]
