/**
 * Generate a UUID without requiring the browser's Secure Context-only
 * `crypto.randomUUID()` helper.
 *
 * Browsers still expose `crypto.getRandomValues()` on plain HTTP, so local
 * and LAN deployments can generate RFC 4122 version 4 UUIDs without weakening
 * randomness. We intentionally do not fall back to Math.random().
 */
export function createUuid(): string {
  const cryptoApi = globalThis.crypto;

  if (typeof cryptoApi?.randomUUID === "function") {
    return cryptoApi.randomUUID();
  }

  if (typeof cryptoApi?.getRandomValues !== "function") {
    throw new Error("Secure random UUID generation is unavailable");
  }

  const bytes = new Uint8Array(16);
  cryptoApi.getRandomValues(bytes);

  // RFC 4122 / RFC 9562 UUIDv4 version and variant bits.
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;

  return Array.from(bytes, (byte, index) => {
    const hex = byte.toString(16).padStart(2, "0");
    return [4, 6, 8, 10].includes(index) ? `-${hex}` : hex;
  }).join("");
}
