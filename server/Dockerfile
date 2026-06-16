FROM node:18-alpine

# Install FFmpeg and basic python dependencies if needed for scripts
RUN apk update && apk add ffmpeg python3

WORKDIR /app

COPY package*.json ./
RUN npm install

COPY . .

EXPOSE 5000
EXPOSE 1234/udp

CMD ["npm", "start"]
