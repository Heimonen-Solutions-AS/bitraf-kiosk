import unittest
from pathlib import Path

from bitraf.parser import parse_xml, to_ms

FIXTURE = (Path(__file__).parent / "fixtures" / "data.xml").read_text()


class ParseXmlTests(unittest.TestCase):
    def setUp(self):
        self.result = parse_xml(FIXTURE, fallback_time_ms=1_754_395_200_000)
        self.metrics = self.result.sample.metrics

    def test_uses_archive_time_over_sensor_timestamps(self):
        self.assertEqual(self.result.sample.time_ms, 1_754_395_200_000)

    def test_scales_values(self):
        self.assertAlmostEqual(self.metrics["airthings0-ietf-hardware.co2"], 485.0)
        self.assertAlmostEqual(self.metrics["airthings0-ietf-hardware.pressure"], 1004.0)
        self.assertAlmostEqual(self.metrics["sensor-pi0.th0"], 23.09)  # hundredths of a degree

    def test_corrects_airthings0_temperature_dropped_digit(self):
        self.assertAlmostEqual(self.metrics["airthings0-ietf-hardware.temperature"], 23.6)

    def test_radon_inside_pressure_component_is_captured(self):
        self.assertAlmostEqual(self.metrics["airthings0-ietf-hardware.radon-short-term-average"], 20.0)

    def test_metadata(self):
        nodes = self.result.metadata["nodes"]
        self.assertEqual(set(nodes), {"airthings0-ietf-hardware", "sensor-pi0"})  # sensor-less node skipped
        self.assertEqual(nodes["airthings0-ietf-hardware"]["model"], "View Plus")
        self.assertEqual(nodes["airthings0-ietf-hardware"]["manufacturer"], "Airthings")
        self.assertIsNone(nodes["airthings0-ietf-hardware"]["location"])  # "NETCONF" location ignored
        self.assertEqual(nodes["sensor-pi0"]["location"], "First floor: 217")
        metrics = self.result.metadata["metrics"]
        self.assertEqual(metrics["airthings0-ietf-hardware.co2"]["unitsDisplay"], "CO2 level")
        self.assertEqual(metrics["airthings0-ietf-hardware.co2"]["valueTimestamp"], to_ms("2026-08-05T12:00:45Z"))

    def test_rejects_documents_without_sensors(self):
        with self.assertRaises(ValueError):
            parse_xml("<config/>")
        with self.assertRaises(ValueError):
            parse_xml("not xml")


class ToMsTests(unittest.TestCase):
    def test_iso_utc(self):
        self.assertEqual(to_ms("2026-08-18T13:04:45Z"), 1_787_058_285_000)

    def test_naive_iso_is_utc(self):
        self.assertEqual(to_ms("2026-08-18T13:04:45"), 1_787_058_285_000)

    def test_unix_seconds_and_millis(self):
        self.assertEqual(to_ms("1787058285"), 1_787_058_285_000)
        self.assertEqual(to_ms("1787058285000"), 1_787_058_285_000)

    def test_garbage(self):
        for bad in (None, "", "0", "42", "yesterday"):
            self.assertIsNone(to_ms(bad), bad)


if __name__ == "__main__":
    unittest.main()
