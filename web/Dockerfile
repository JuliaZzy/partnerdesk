ARG NODE_IMAGE=node:22-alpine
FROM ${NODE_IMAGE}

WORKDIR /app

ARG NPM_REGISTRY=https://registry.npmjs.org
COPY package.json package-lock.json ./
# install (not ci): the lockfile was written by an older npm and Node 22's npm
# wants @types/node as a Vite peer, which `npm ci` treats as a mismatch.
RUN npm install --registry="${NPM_REGISTRY}"

COPY . .

EXPOSE 5173

CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0", "--port", "5173"]
