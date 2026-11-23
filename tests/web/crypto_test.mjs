// node tests/web/crypto_test.mjs <topic> <locked hex from teamcrypt.py>
import { readFileSync } from "node:fs";
import { webcrypto } from "node:crypto";
import assert from "node:assert";
if (!globalThis.crypto) globalThis.crypto = webcrypto;
new Function(readFileSync(new URL("../../web/crypto.js", import.meta.url), "utf8"))();
const T = globalThis.TeamCrypt, H = (s) => Uint8Array.from(Buffer.from(s, "hex"));
// RFC 8439 2.5.2 and 2.8.2
assert.equal(T.hex(T.poly1305(H("85d6be7857556d337f4452fe42d506a80103808afb0db2fd4abff6af4149f51b"),
  new TextEncoder().encode("Cryptographic Forum Research Group"))), "a8061dc1305136c6c22b8baf0c0127a9");
const key = Uint8Array.from({ length: 32 }, (_, i) => 0x80 + i);
const ct = H("d31a8d34648e60db7b86afbc53ef7ec2a4aded51296e08fea9e2b5a736ee62d63dbea45e8ca9671282fafb69da92728b1a71de0a9e060b2905d6a5b67ecd3b3692ddbd7f2d778b8c9803aee328091b58fab324e4fad675945585808b4831d7bc3ff4def08e4b7a9de576d26586cec64b6116" + "1ae10b594f09e26a7e902ecbd0600691");
const pt = T.aeadDecrypt(key, H("070000004041424344454647"), ct, H("50515253c0c1c2c3c4c5c6c7"));
assert.ok(new TextDecoder().decode(pt).startsWith("Ladies and Gentlemen of the class of '99"));
// a message locked by the tracker (teamcrypt.py)
const [topic, blob] = process.argv.slice(2);
const s = await T.secrets("cross test 7");
assert.equal(s.topic, topic);
const m = T.unlock(s.key, H(blob));
assert.equal(m.name, "Ann"); assert.equal(m.total, 42);
assert.equal(T.unlock((await T.secrets("wrong")).key, H(blob)), null);
console.log("web crypto OK");
