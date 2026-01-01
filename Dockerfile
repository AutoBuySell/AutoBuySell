FROM node:20-alpine

WORKDIR /app

# Install dependencies based on package.json
COPY package.json package-lock.json* ./
RUN npm install

# Copy source code
COPY . .

# Build not needed for dev, but good practice to have available or just run dev
CMD ["npm", "run", "dev"]
