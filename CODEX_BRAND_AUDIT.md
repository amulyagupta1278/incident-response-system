# Codex Brand Audit

This repo uses Codex-first product wording while preserving required OpenAI API
runtime identifiers.

## Forbidden Source Terms

Source, docs, tests, and config must not contain these exact vendor/model terms
(shown here with bracketed characters so the audit file itself stays clean):

- `clau[d]e`
- `anthropi[c]`
- `sonne[t]`
- `haik[u]`
- `opu[s]`

## Allowed Runtime Terms

Keep these when they refer to real code, configuration, dependencies, or tests:

- `OPENAI_API_KEY`
- `OPENAI_MODEL`
- `OPENAI_EMBEDDING_MODEL`
- `OPENAI_TIMEOUT_SECONDS`
- `openai>=...`
- `AsyncOpenAI`
- provider value `"openai"`
- default model names such as `gpt-4o`

## Preferred Product Wording

Use `Codex`, `Codex LLM runtime`, or `Codex-style agent reasoning` for
user-facing product descriptions, docs, and UI copy.

## Audit Commands

```bash
rg -n -i "clau[d]e|anthropi[c]|sonne[t]|haik[u]|opu[s]" . \
  -g '!web/node_modules/**' -g '!web/.next/**' -g '!venv/**' \
  -g '!__pycache__/**' -g '!*.pyc' -g '!.git/**' -g '!.env' -g '!.env.*'

rg -n -i "chatgp[t]|gpt-4o|openai|codex" . \
  -g '!web/node_modules/**' -g '!web/.next/**' -g '!venv/**' \
  -g '!__pycache__/**' -g '!*.pyc' -g '!.git/**' -g '!.env' -g '!.env.*'
```

Never print `.env` secret values in review output.
