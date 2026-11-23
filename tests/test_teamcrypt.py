"""teamcrypt against the RFC test vectors, and team messages locked with the team code."""
import os
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import teamcrypt as tc  # noqa: E402

H = bytes.fromhex


class RfcVectors(unittest.TestCase):
    def test_chacha20_block_rfc8439_2_3_2(self):
        key = bytes(range(32))
        nonce = H("000000090000004a00000000")
        self.assertEqual(tc.chacha20_block(key, 1, nonce).hex(),
                         "10f1e7e4d13b5915500fdd1fa32071c4c7d1f4c733c068030422aa9ac3d46c4e"
                         "d2826446079faa0914c2d705d98b02a2b5129cd1de164eb9cbd083e8a2503c4e")

    def test_poly1305_rfc8439_2_5_2(self):
        key = H("85d6be7857556d337f4452fe42d506a80103808afb0db2fd4abff6af4149f51b")
        self.assertEqual(tc.poly1305(key, b"Cryptographic Forum Research Group").hex(),
                         "a8061dc1305136c6c22b8baf0c0127a9")

    def test_aead_rfc8439_2_8_2(self):
        pt = (b"Ladies and Gentlemen of the class of '99: If I could offer you only one tip for the future, "
              b"sunscreen would be it.")
        key = bytes(range(0x80, 0xA0))
        nonce = H("070000004041424344454647")
        aad = H("50515253c0c1c2c3c4c5c6c7")
        out = tc.aead_encrypt(key, nonce, pt, aad)
        self.assertEqual(out[:16].hex(), "d31a8d34648e60db7b86afbc53ef7ec2")
        self.assertEqual(out[-16:].hex(), "1ae10b594f09e26a7e902ecbd0600691")
        self.assertEqual(tc.aead_decrypt(key, nonce, out, aad), pt)
        bad = bytearray(out); bad[5] ^= 1
        self.assertIsNone(tc.aead_decrypt(key, nonce, bytes(bad), aad))

    def test_x25519_rfc7748_5_2(self):
        k = H("a546e36bf0527c9d3b16154b82465edd62144c0ac1fc5a18506a2244ba449ac4")
        u = H("e6db6867583030db3594c1a424b15f7c726624ec26b3353b10a903a6d0ab1c4c")
        self.assertEqual(tc.x25519(k, u).hex(), "c3da55379de9c6908e94ea4df28d084f32eccf03491c71f754b4075577a28552")

    def test_x25519_rfc7748_6_1(self):
        a = H("77076d0a7318a57d3c16c17251b26645df4c2f87ebc0992ab177fba51db92c2a")
        b = H("5dab087e624a8a4b79e17f8b83800ee66f3bb1292618b6fd1c2f8b27ff88e0eb")
        self.assertEqual(tc.x25519_public(a).hex(), "8520f0098930a754748b7ddcb43ef75a0dbf3a0d26381af4eba4a98eaa9b4e6a")
        self.assertEqual(tc.x25519(a, tc.x25519_public(b)).hex(),
                         "4a5d9d5ba4ce2de1728e3bf480350f25e07e21c947d19e3376f09b3c1e161742")


class TeamMessages(unittest.TestCase):
    def test_code_spelling_doesnt_matter_and_codes_differ(self):
        self.assertEqual(tc.team_secrets("Siblings 42"), tc.team_secrets("siblings42"))
        self.assertNotEqual(tc.team_secrets("siblings42")[1], tc.team_secrets("siblings43")[1])
        self.assertEqual(tc.team_secrets(""), ("", b""))
        topic, key = tc.team_secrets("x")
        self.assertEqual(len(topic), 24)
        self.assertNotIn(topic, key.hex())

    def test_round_trip_and_wrong_code(self):
        _, key = tc.team_secrets("our squad code")
        _, other = tc.team_secrets("someone else")
        msg = {"app": "chesttracker", "name": "Ann", "total": 12}
        blob = tc.lock(key, msg)
        self.assertNotIn(b"Ann", blob)
        self.assertEqual(tc.unlock(key, blob), msg)
        self.assertIsNone(tc.unlock(other, blob))
        self.assertIsNone(tc.unlock(key, b'{"app": "chesttracker"}'))   # a plain (unlocked) message
        self.assertNotEqual(tc.lock(key, msg), blob)                      # a new nonce every time

    def test_big_message_is_quick(self):
        _, key = tc.team_secrets("speed")
        msg = {"games": [[f"session{i:030d}", i] for i in range(400)]}
        t = time.perf_counter()
        self.assertEqual(tc.unlock(key, tc.lock(key, msg)), msg)
        self.assertLess(time.perf_counter() - t, 0.5)

    def test_sealed_report(self):
        secret = os.urandom(32)
        pub = tc.x25519_public(secret)
        blob = tc.seal_to(pub, b"report text")
        self.assertEqual(tc.open_sealed(secret, blob), b"report text")
        self.assertIsNone(tc.open_sealed(os.urandom(32), blob))


if __name__ == "__main__":
    unittest.main()
