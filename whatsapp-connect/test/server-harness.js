const fs = require("node:fs");
const vm = require("node:vm");

async function loadServer(options = {}) {
  const handlers = new Map(), events = new Map(), launches = [], sent = [], timers = [];
  const socket = {
    user: { id: "15550000000:1@s.whatsapp.net" },
    ev: { on: (name, handler) => events.set(name, handler) },
    sendMessage: async (jid, payload) => { sent.push({ jid, ...payload }); return { key: { id: "bot-id" } }; },
    logout: options.logout || (async () => events.get("connection.update")({
      connection: "close", lastDisconnect: { error: { output: { statusCode: 401 } } },
    })),
  };
  const app = { use() {}, get: (route, ...args) => handlers.set(`GET ${route}`, args.at(-1)),
    post: (route, ...args) => handlers.set(`POST ${route}`, args.at(-1)),
    put: (route, ...args) => handlers.set(`PUT ${route}`, args.at(-1)),
    listen: (_port, _host, callback) => callback() };
  const env = { WA_TOKEN: "synthetic-token-01234567890123456789", WA_PASSWORD: "synthetic-password-01234567890123456789",
    WA_SEND_SECRET: "synthetic-send-01234567890123456789", HERMES_BIN: "/controlled/hermes",
    HERMES_OS_HOME: "/controlled", HERMES_PATH: "/controlled/bin", LANG: "C", OPENAI_API_KEY: "synthetic-model",
    SHOPIFY_CLIENT_SECRET: "synthetic-commerce", CONSOLE_SESSION_SECRET: "synthetic-console",
    PYTHONPATH: "/injected", NODE_OPTIONS: "--injected", ...options.env };
  const logger = { info() {}, error() {} };
  const context = vm.createContext({ process: { env, pid: 1, exit: options.exit || (() => {}) },
    setTimeout: callback => timers.push(callback), console,
    require(name) {
      if (name === "express") return Object.assign(() => app, { json: () => {} });
      if (name === "qrcode") return { toDataURL: async () => "synthetic-qr" };
      if (name === "pino") return () => logger;
      if (name === "fs") return { readFileSync: () => '{"mode":"linked"}', rmSync() {}, mkdirSync() {} };
      if (name === "child_process") return { execFile: (bin, args, config, callback) => {
        launches.push({ bin, args: Array.from(args), config: JSON.parse(JSON.stringify(config)) });
        callback(null, "Private owner answer");
      } };
      if (name === "@whiskeysockets/baileys") return { default: () => socket,
        useMultiFileAuthState: options.auth || (async () => ({ state: {}, saveCreds() {} })),
        DisconnectReason: { loggedOut: 401 }, Browsers: { ubuntu: () => [] },
        fetchLatestBaileysVersion: async () => ({ version: [] }) };
      if (name.startsWith("./")) return require(`..${name.slice(1)}`);
      return require(name);
    } });
  const source = fs.readFileSync(`${__dirname}/../server.js`, "utf8").split("const PAGE =")[0];
  vm.runInContext(source + '\nthis.observed = statusPayload;', context);
  for (let i = 0; i < 5; i++) await Promise.resolve();
  return { context, handlers, events, launches, sent, timers, socket,
    observed: () => JSON.parse(JSON.stringify(context.observed())) };
}

module.exports = { loadServer };
