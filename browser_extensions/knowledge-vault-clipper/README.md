# Knowledge Vault Clipper

Load this folder as an unpacked Chrome extension.

Default target API:

- `http://127.0.0.1:8001/api/vault/clip`

Workflow:

1. Open the popup on any page.
2. Optionally add a topic hint or operator notes.
3. Click `Clip Current Page`.

The extension prefers selected text. If nothing is selected, it falls back to `article`, `main`, or page body text and queues the clip into Capybara Home's `knowledge_vault`.
