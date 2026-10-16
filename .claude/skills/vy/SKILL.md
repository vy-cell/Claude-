---
name: vy
description: Talk to Vy's digital twin. Get a soundbite, an email reply, or a decision written the way Vy would. Usage - /vy <request>, optionally prefixed with soundbite:, email:, reply:, or decide:.
---

Hand the user's request to the `vy` subagent (Agent tool, subagent_type "vy") and pass `$ARGUMENTS` through word for word. If the user pasted an email or message to answer, include the full text.

- If no mode prefix is given, let the agent choose.
- Return the agent's output exactly as written. Don't add commentary, and don't rewrite it into your own voice.
- If `$ARGUMENTS` is empty, ask what Vy should respond to, in one line.
