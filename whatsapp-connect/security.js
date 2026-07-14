"use strict";

const crypto = require("node:crypto");
const net = require("node:net");

const PLACEHOLDERS = new Set([
  "changeme",
  "change-me",
  "password",
  "secret",
  "chaim123",
  "__wa_token__",
  "__wa_send_secret__",
]);

function validateSecret(name, value, minLength = 32) {
  const normalized = typeof value === "string" ? value.trim() : "";
  if (normalized.length < minLength) {
    throw new Error(`${name} must be set to a unique secret of at least ${minLength} characters`);
  }
  const lower = normalized.toLowerCase();
  if (PLACEHOLDERS.has(lower) || lower.startsWith("replace-with-")) {
    throw new Error(`${name} must not use a placeholder or default value`);
  }
  return normalized;
}

function clientAddress(req) {
  const remote = (req.socket && req.socket.remoteAddress) || "unknown";
  const loopback = remote === "127.0.0.1" || remote === "::1" || remote === "::ffff:127.0.0.1";
  if (!loopback) return remote;

  const rawForwarded = req.headers && req.headers["x-forwarded-for"];
  const forwarded = Array.isArray(rawForwarded) ? rawForwarded[0] : rawForwarded;
  const first = typeof forwarded === "string" ? forwarded.split(",", 1)[0].trim() : "";
  return net.isIP(first) ? first : remote;
}

function extractCredential(authorization) {
  if (typeof authorization !== "string") return null;
  const match = authorization.match(/^([^\s]+)\s+(.+)$/);
  if (!match) return null;
  const scheme = match[1].toLowerCase();
  const value = match[2].trim();

  if (scheme === "bearer") return value || null;
  if (scheme !== "basic") return null;

  if (!/^[A-Za-z0-9+/]+={0,2}$/.test(value) || value.length % 4 !== 0) return null;
  try {
    const decoded = Buffer.from(value, "base64").toString("utf8");
    const separator = decoded.indexOf(":");
    return separator >= 0 ? decoded.slice(separator + 1) : null;
  } catch (_error) {
    return null;
  }
}

function constantTimeEqual(candidate, expected) {
  if (typeof candidate !== "string" || typeof expected !== "string") return false;
  const candidateDigest = crypto.createHash("sha256").update(candidate).digest();
  const expectedDigest = crypto.createHash("sha256").update(expected).digest();
  return crypto.timingSafeEqual(candidateDigest, expectedDigest);
}

function isAuthorized(authorization, expectedSecret) {
  return constantTimeEqual(extractCredential(authorization), expectedSecret);
}

function createSendAuth(expectedSecret, onReject = null) {
  expectedSecret = validateSecret("WA_SEND_SECRET", expectedSecret);
  return function requireSendAuth(req, res, next) {
    if (isAuthorized(req.headers && req.headers.authorization, expectedSecret)) {
      return next();
    }
    if (typeof onReject === "function") {
      try { onReject(req); } catch (_error) { /* logging must never weaken the auth gate */ }
    }
    res.set("WWW-Authenticate", 'Bearer realm="Buttons Bebe WhatsApp alerts"');
    return res.status(401).json({ error: "unauthorized" });
  };
}

module.exports = {
  clientAddress,
  createSendAuth,
  extractCredential,
  isAuthorized,
  validateSecret,
};
