/**
 * Inbox WebMCP (document.modelContext) — browser UI tools for the live inbox.
 *
 * Naming: short verbs (`select_ticket`, `use_draft`, …). These are Document-scoped
 * chrome drivers on this page, not helpdesk.* server/MCP tools. Never register a
 * Send / refund / cancel tool here — Human Send only; Draft strip Use/Regenerate/Dismiss.
 *
 * Prefer document.modelContext; fall back to navigator.modelContext for older
 * Chrome 146–149 previews. Progressive enhancement when modelContext is absent.
 */

import { VIEW_IDS } from "./view-model.js";

/**
 * @typedef {{
 *   registerTool(tool: object, options?: { signal?: AbortSignal, exposedTo?: string[] }): Promise<void> | void,
 *   unregisterTool?(name: string): void,
 * }} ModelContext
 */

/**
 * @returns {ModelContext | null}
 */
export function resolveModelContext(
  doc = typeof document !== "undefined" ? document : undefined,
  nav = typeof navigator !== "undefined" ? navigator : undefined,
) {
  return doc?.modelContext || nav?.modelContext || null;
}

/**
 * Register tools for the lifetime of dispose(). Awaits Chrome 151+ Promise
 * registration; safe when registerTool is sync.
 *
 * @param {object[]} tools
 * @param {{ modelContext?: ModelContext | null, exposedTo?: string[] }} [options]
 */
export function registerWebMcpTools(tools, options = {}) {
  const modelContext =
    options.modelContext !== undefined
      ? options.modelContext
      : resolveModelContext();
  if (!modelContext) {
    return {
      ready: Promise.resolve(),
      dispose() {},
      registeredNames: /** @type {string[]} */ ([]),
      available: false,
    };
  }

  const controller = new AbortController();
  const registeredNames = /** @type {string[]} */ ([]);

  const ready = Promise.all(
    (tools || []).map(async (tool) => {
      try {
        await modelContext.registerTool(tool, {
          signal: controller.signal,
          exposedTo: options.exposedTo,
        });
        registeredNames.push(tool.name);
      } catch (error) {
        console.error(`WebMCP: failed to register "${tool?.name}"`, error);
      }
    }),
  ).then(() => undefined);

  return {
    ready,
    registeredNames,
    available: true,
    dispose() {
      for (const name of registeredNames.splice(0).reverse()) {
        try {
          modelContext.unregisterTool?.(name);
        } catch {
          // ignore stale cleanup
        }
      }
      controller.abort();
    },
  };
}

function snapSummary(snap) {
  if (!snap || typeof snap !== "object") return {};
  return {
    viewId: snap.viewId ?? null,
    selectedId: snap.selectedId ?? null,
    strip: Boolean(snap.strip),
    bodyLen: String(snap.body || "").length,
    macrosOpen: Boolean(snap.macrosOpen),
    summarize: Boolean(snap.summarize),
  };
}

function truncate(text, max = 1400) {
  const s = String(text ?? "");
  if (s.length <= max) return s;
  return `${s.slice(0, max - 1)}…`;
}

/**
 * Build WebMCP tool definitions that call the inbox organ APIs.
 * Execute updates visible UI via organ methods (which remount after mount),
 * then resolves with a short JSON status for the agent.
 *
 * @param {{
 *   selectView(viewId: string): Promise<object> | object,
 *   selectTicket(id: string): Promise<object> | object,
 *   insertDraft(): object,
 *   regenerateDraft(): Promise<object>,
 *   discardStrip(): object,
 *   requestSummarize?: () => Promise<object>,
 *   openMacros?: () => object,
 *   applyMacro?: (macroId: string, mode?: string) => Promise<object>,
 *   snapshot?: () => object,
 * }} organ
 */
export function buildInboxWebMcpTools(organ) {
  if (!organ) return [];

  /** @type {object[]} */
  const tools = [
    {
      name: "select_view",
      title: "Select inbox view",
      description:
        "Switch the inbox list filter to a view (open, mine, unassigned, all, snoozed, or closed).",
      inputSchema: {
        type: "object",
        properties: {
          viewId: {
            type: "string",
            enum: [...VIEW_IDS],
            description: "First-party inbox view id.",
          },
        },
        required: ["viewId"],
      },
      annotations: {
        readOnlyHint: true,
        untrustedContentHint: false,
        consequentialHint: false,
      },
      async execute({ viewId }, { signal } = {}) {
        if (signal?.aborted) throw new DOMException("Aborted", "AbortError");
        const id = String(viewId || "");
        if (!VIEW_IDS.includes(id)) {
          return JSON.stringify({ ok: false, error: `Unknown viewId: ${id}` });
        }
        const snap = await organ.selectView(id);
        return truncate(JSON.stringify({ ok: true, ...snapSummary(snap || organ.snapshot?.()) }));
      },
    },
    {
      name: "select_ticket",
      title: "Select ticket",
      description:
        "Select a ticket in the inbox list by ticket id so the thread, composer, and rail update.",
      inputSchema: {
        type: "object",
        properties: {
          ticketId: {
            type: "string",
            description: "First-party inbox ticket id.",
          },
        },
        required: ["ticketId"],
      },
      annotations: {
        readOnlyHint: true,
        untrustedContentHint: false,
        consequentialHint: false,
      },
      async execute({ ticketId }, { signal } = {}) {
        if (signal?.aborted) throw new DOMException("Aborted", "AbortError");
        const id = String(ticketId || "").trim();
        if (!id) {
          return JSON.stringify({ ok: false, error: "ticketId is required" });
        }
        const snap = await organ.selectTicket(id);
        return truncate(JSON.stringify({ ok: true, ...snapSummary(snap || organ.snapshot?.()) }));
      },
    },
    {
      name: "use_draft",
      title: "Use AI draft",
      description:
        "Insert the AI draft strip into the reply box (Use draft). Does not send.",
      inputSchema: { type: "object", properties: {} },
      annotations: {
        readOnlyHint: false,
        untrustedContentHint: false,
        consequentialHint: false,
      },
      async execute(_input, { signal } = {}) {
        if (signal?.aborted) throw new DOMException("Aborted", "AbortError");
        const snap = organ.insertDraft();
        return truncate(JSON.stringify({ ok: true, action: "use_draft", ...snapSummary(snap) }));
      },
    },
    {
      name: "regenerate_draft",
      title: "Regenerate AI draft",
      description:
        "Regenerate the AI draft strip for the selected ticket. Does not send.",
      inputSchema: { type: "object", properties: {} },
      annotations: {
        readOnlyHint: false,
        untrustedContentHint: false,
        consequentialHint: false,
      },
      async execute(_input, { signal } = {}) {
        if (signal?.aborted) throw new DOMException("Aborted", "AbortError");
        const snap = await organ.regenerateDraft();
        return truncate(
          JSON.stringify({ ok: true, action: "regenerate_draft", ...snapSummary(snap) }),
        );
      },
    },
    {
      name: "dismiss_draft",
      title: "Dismiss AI draft",
      description: "Dismiss the AI draft strip without inserting it. Does not send.",
      inputSchema: { type: "object", properties: {} },
      annotations: {
        readOnlyHint: false,
        untrustedContentHint: false,
        consequentialHint: false,
      },
      async execute(_input, { signal } = {}) {
        if (signal?.aborted) throw new DOMException("Aborted", "AbortError");
        const snap = organ.discardStrip();
        return truncate(
          JSON.stringify({ ok: true, action: "dismiss_draft", ...snapSummary(snap) }),
        );
      },
    },
  ];

  if (typeof organ.requestSummarize === "function") {
    tools.push({
      name: "summarize_thread",
      title: "Summarize thread",
      description:
        "Show a short mute summary of the selected thread above the reply box.",
      inputSchema: { type: "object", properties: {} },
      annotations: {
        readOnlyHint: true,
        untrustedContentHint: true,
        consequentialHint: false,
      },
      async execute(_input, { signal } = {}) {
        if (signal?.aborted) throw new DOMException("Aborted", "AbortError");
        const snap = await organ.requestSummarize();
        return truncate(
          JSON.stringify({ ok: true, action: "summarize_thread", ...snapSummary(snap) }),
        );
      },
    });
  }

  if (typeof organ.openMacros === "function") {
    tools.push({
      name: "open_macros",
      title: "Open macros",
      description: "Open the composer macros search so a macro can be chosen.",
      inputSchema: { type: "object", properties: {} },
      annotations: {
        readOnlyHint: true,
        untrustedContentHint: false,
        consequentialHint: false,
      },
      async execute(_input, { signal } = {}) {
        if (signal?.aborted) throw new DOMException("Aborted", "AbortError");
        const snap = organ.openMacros();
        return truncate(
          JSON.stringify({ ok: true, action: "open_macros", ...snapSummary(snap) }),
        );
      },
    });
  }

  if (typeof organ.applyMacro === "function") {
    tools.push({
      name: "apply_macro",
      title: "Apply macro",
      description:
        "Apply a macro into the reply box by macro id (replace or append). Does not send.",
      inputSchema: {
        type: "object",
        properties: {
          macroId: {
            type: "string",
            description: "Macro id (e.g. shipping-delay).",
          },
          mode: {
            type: "string",
            enum: ["replace", "append"],
            description: "Replace the body or append after it.",
          },
        },
        required: ["macroId"],
      },
      annotations: {
        readOnlyHint: false,
        untrustedContentHint: false,
        consequentialHint: false,
      },
      async execute({ macroId, mode }, { signal } = {}) {
        if (signal?.aborted) throw new DOMException("Aborted", "AbortError");
        const id = String(macroId || "").trim();
        if (!id) {
          return JSON.stringify({ ok: false, error: "macroId is required" });
        }
        const applyMode = mode === "append" ? "append" : "replace";
        const snap = await organ.applyMacro(id, applyMode);
        return truncate(
          JSON.stringify({
            ok: true,
            action: "apply_macro",
            macroId: id,
            mode: applyMode,
            ...snapSummary(snap),
          }),
        );
      },
    });
  }

  return tools;
}

/**
 * Register inbox WebMCP tools after the organ is mounted/ready.
 * Returns a handle with ready + dispose; no-op when modelContext is missing.
 *
 * @param {object} organ
 * @param {{ modelContext?: ModelContext | null }} [options]
 */
export function registerInboxWebMcp(organ, options = {}) {
  const tools = buildInboxWebMcpTools(organ);
  return registerWebMcpTools(tools, options);
}

export { VIEW_IDS as INBOX_WEBMCP_VIEW_IDS } from "./view-model.js";
