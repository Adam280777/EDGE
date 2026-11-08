"""Offline tests for metadata budget matching; no package/model/network use."""

import unittest
from unittest.mock import patch

from estimate_budget import wheel_size


class BudgetMetadataTests(unittest.TestCase):
    def item(self, host="files.pythonhosted.org", digest="abc"):
        return {
            "metadata": {"name": "example", "version": "1.0"},
            "download_info": {
                "url": f"https://{host}/example-1.0-py3-none-any.whl",
                "archive_info": {"hashes": {"sha256": digest}} if digest else {},
            },
        }

    def release(self, digest="abc"):
        return {"urls": [{
            "filename": "example-1.0-py3-none-any.whl",
            "digests": {"sha256": digest},
            "size": 123,
            "url": "https://files.pythonhosted.org/example-1.0-py3-none-any.whl",
        }]}

    def test_matching_hash_preserves_exact_size(self):
        with patch("estimate_budget.public_json", return_value=self.release()):
            result = wheel_size(self.item())
        self.assertEqual(result["download_bytes"], 123)
        self.assertEqual(result["sha256"], "abc")
        self.assertFalse(result["substituted_unhashed_mirror_requires_target_reresolution"])

    def test_mismatched_hash_is_not_accepted(self):
        with (
            patch("estimate_budget.public_json", return_value=self.release("different")),
            self.assertRaises(StopIteration),
        ):
            wheel_size(self.item())

    def test_unhashed_mirror_replacement_is_explicit(self):
        with patch("estimate_budget.public_json", return_value=self.release()):
            result = wheel_size(self.item("download.pytorch.org", None))
        self.assertTrue(result["substituted_unhashed_mirror_requires_target_reresolution"])
        self.assertTrue(result["url"].startswith("https://files.pythonhosted.org/"))

    def test_unknown_host_never_calls_network(self):
        with (
            patch("estimate_budget.public_json") as request,
            self.assertRaises(ValueError),
        ):
            wheel_size(self.item("example.invalid"))
        request.assert_not_called()


if __name__ == "__main__":
    unittest.main()
