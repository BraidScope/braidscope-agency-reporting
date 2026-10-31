# Backend image. Python 3.12 plus Node 22 for the optional Codex and
# Claude Code CLIs (Ask AI "sign in with ChatGPT / Claude").
#
# Node comes from the official image instead of Debian's package, which is
# Node 20 and pulls in ~370 packages; the Claude CLI needs Node 22+.
# Build with --build-arg WITH_AI_CLIS=false to skip the CLIs entirely.

FROM node:26.12.0-bookworm-slim AS node

FROM python:3.12-slim-bookworm

ARG WITH_AI_CLIS=true
# Short git hash of the deployed commit; surfaced by the health endpoint.
ARG GIT_COMMIT=unknown
ENV GIT_COMMIT=$GIT_COMMIT

COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s /usr/local/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -s /usr/local/lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx \
    && if [ "$WITH_AI_CLIS" = "true" ]; then \
         npm install -g --no-audit --no-fund @openai/codex @anthropic-ai/claude-code \
         && npm cache clean --force; \
       fi

WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
