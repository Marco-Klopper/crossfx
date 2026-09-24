"""
app.security.encryption — the whole private-key story for the brief's
"Security & private-key protection" criterion.

It had no direct tests at all. decrypt_seed was never called by the suite
except incidentally through xrpl_service, and nothing asserted that a
tampered ciphertext or the wrong key is rejected rather than quietly
returning something. That is the property the module exists for: if a
modified row could be decrypted, encrypting the seeds would be decoration.
"""
import pytest
from cryptography.fernet import Fernet, InvalidToken

from app.security.encryption import decrypt_seed, encrypt_seed


class TestRoundTrip:
    def test_a_seed_survives_encrypt_then_decrypt(self):
        seed = "sEdV1nkMh3Nt2vGqRbTfYxWpZcKjLuA"
        assert decrypt_seed(encrypt_seed(seed)) == seed

    def test_the_ciphertext_does_not_contain_the_seed(self):
        seed = "sEdV1nkMh3Nt2vGqRbTfYxWpZcKjLuA"
        ciphertext = encrypt_seed(seed)
        assert seed not in ciphertext

    def test_encrypting_twice_gives_different_ciphertexts(self):
        """
        Fernet includes a random IV, so identical seeds do not produce
        identical rows. Without that, two pool wallets sharing a seed
        would be visible as such from the database alone.
        """
        seed = "sEdV1nkMh3Nt2vGqRbTfYxWpZcKjLuA"
        assert encrypt_seed(seed) != encrypt_seed(seed)

    def test_handles_a_seed_with_non_ascii_characters(self):
        seed = "sEd—naïve—seed—ünicode"
        assert decrypt_seed(encrypt_seed(seed)) == seed


class TestTampering:
    """
    Fernet is authenticated encryption. These pin that the authentication
    half actually does something — a ciphertext that has been edited must
    raise, not decrypt to a different seed.
    """

    def test_a_modified_ciphertext_is_rejected(self):
        ciphertext = encrypt_seed("sEdV1nkMh3Nt2vGqRbTfYxWpZcKjLuA")
        # Flip one character of the payload, leaving the length intact.
        position = len(ciphertext) // 2
        original = ciphertext[position]
        replacement = "A" if original != "A" else "B"
        tampered = ciphertext[:position] + replacement + ciphertext[position + 1 :]

        with pytest.raises(InvalidToken):
            decrypt_seed(tampered)

    def test_a_truncated_ciphertext_is_rejected(self):
        ciphertext = encrypt_seed("sEdV1nkMh3Nt2vGqRbTfYxWpZcKjLuA")
        with pytest.raises(InvalidToken):
            decrypt_seed(ciphertext[:-8])

    def test_plain_text_is_not_accepted_as_a_ciphertext(self):
        """
        The failure mode worth ruling out: a row written before encryption
        existed, or by a script that forgot to encrypt, must not sail
        through as though it were fine.
        """
        with pytest.raises(InvalidToken):
            decrypt_seed("sEdV1nkMh3Nt2vGqRbTfYxWpZcKjLuA")

    def test_a_seed_encrypted_under_another_key_is_rejected(self):
        """
        PRIVATE_KEY_ENCRYPTION_KEY lives outside the database precisely so
        that the encrypted rows are useless without it. This is that claim,
        as a test: a ciphertext from a different key does not decrypt.
        """
        other = Fernet(Fernet.generate_key())
        foreign = other.encrypt(b"sEdV1nkMh3Nt2vGqRbTfYxWpZcKjLuA").decode()

        with pytest.raises(InvalidToken):
            decrypt_seed(foreign)
