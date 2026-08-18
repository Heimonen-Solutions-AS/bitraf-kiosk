import unittest

from bitraf.poller import Poller


class TimeFromUrlTests(unittest.TestCase):
    def test_archive_path(self):
        url = "https://lightside-instruments.com/bitraf/data/2026/08/18/13/14/data.xml"
        self.assertEqual(Poller.time_from_url(url), 1_787_058_840_000)

    def test_garbage(self):
        self.assertIsNone(Poller.time_from_url("https://x/data.xml"))


if __name__ == "__main__":
    unittest.main()
