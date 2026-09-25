# Develop and run locally

Run these from the repository root, identified by AGENTS.md and console-src/inbox2/live_api.py. Check git status --short --branch first; this checkout may be a feature branch rather than deployed main.

## Inbox preview without credentials

    python3 skills/buttonsbebe-support-webapp/scripts/serve_inbox_preview.py --port 8878
    # open http://127.0.0.1:8878/inbox/

The skill's standard-library helper serves only the current Inbox assets and a synthetic read-only API ticket on 127.0.0.1. It lets an agent inspect layout and interaction without Gorgias, Shopify, a console session, or a database. The page may request its Google Fonts when the browser has internet access. Stop it with Ctrl-C. It is a UI preview, not a test of live provider integration or authorization. For browser automation, open http://127.0.0.1:8878/inbox/?ticket=gorgias%3A123.

The older console-src/inbox2/README.md command using python3 -m http.server with --directory console-src does not route /inbox/ to inbox2/ in this source checkout; the old console-src/inbox/index.html is gone. Use the helper above for that route. The browser test files intercept /inbox/api/helpdesk, so this helper can also serve their static page. Their default test URL is port 8878.

## Edit and verify by component

| Change | Focused check |
| --- | --- |
| Inbox HTML/CSS/JS | node --check console-src/inbox2/app.js and node --check console-src/inbox2/icons.js; inspect the preview at desktop and mobile widths. |
| Inbox API/customer queue | python -m unittest discover -s console-src/inbox2/tests -p 'test_*.py' in a Python 3.12 environment with pinned Inbox requirements. |
| Shared projection/Shopify rail | python -m unittest discover -s console-src/inbox/tests -p 'test_*.py'; inspect the affected API tests too. |
| Console theme/brand | Edit console-src/support-theme.css or the brand source; use python3 tools/build_support_theme.py and python3 tools/sync_console_brand.py, then the corresponding --check commands. Generated embedded CSS belongs to that pipeline. |
| Console API/auth/actions | Run the relevant webhook/test_*.py module with PYTHONPATH=webhook/src; use the repo's locked webhook environment. |
| Processor/draft behavior | Run focused processor/test_*.py; use testing/HOW-TO-RUN.md for the 48-scenario live-model evaluation before release. |

Pinned runtimes: Python at least 3.12; console-src/inbox/requirements.lock for Inbox, webhook/uv.lock and processor/uv.lock for their uv environments; Node for frontend checks and Node services. A system Python lacking FastAPI is not the Inbox test environment. Do not copy a production .env into this checkout to make local preview work.

Browser tests in console-src/inbox2/tests/layout.mjs and customer-loading.mjs need Playwright and Chromium. With the preview helper running on 8878, use node console-src/inbox2/tests/layout.mjs and node console-src/inbox2/tests/customer-loading.mjs. They use synthetic responses and save screenshots under the system temporary directory. Set PLAYWRIGHT_MODULE only if Playwright is installed outside the project's module path.

For an Inbox edit, compare the actual page at narrow and wide widths, keep the reply editor visible, and verify loading, empty, auth-expired, and failure states where relevant. console-src/inbox2/DESIGN.md is the page's visual reference. For a console edit, use the authenticated console in a permitted environment; its single HTML file cannot provide full behavior through the Inbox preview helper.

## Real local backend

console-src/inbox2/live_api.py can start on 127.0.0.1:8767 in the pinned environment, but it immediately initializes local state and its background sync targets the fixed read-only MCP at 127.0.0.1:8079. The Shopify worker uses a protected environment and separate snapshot path. Running those services requires an intentionally configured, isolated environment and is not needed for frontend work. Follow their service units and console-src/inbox2/README.md when working on those integrations.
