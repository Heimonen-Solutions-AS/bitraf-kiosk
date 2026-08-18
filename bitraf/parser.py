"""Parse one archived data.xml (IETF hardware + lsi-thermometers YANG data)
into a flat {node.sensor: value} row plus node/sensor metadata."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import time
from typing import Dict, Iterator, List, Optional, Tuple
import xml.etree.ElementTree as ET

# On 2026-08-05/06 the airthings0 feed dropped a digit in temperature
# (2360 milli = 2.36 °C instead of 23.6). Values in this range are scaled ×10.
TEMPERATURE_FIX_NODE = "airthings0-ietf-hardware"
TEMPERATURE_FIX_RANGE = (-10.0, 10.0)

SCALE_FACTORS = {
    "yocto": 1e-24, "zepto": 1e-21, "atto": 1e-18, "femto": 1e-15,
    "pico": 1e-12, "nano": 1e-9, "micro": 1e-6, "milli": 1e-3,
    "centi": 1e-2, "deci": 1e-1, "units": 1.0, "deca": 1e1,
    "hecto": 1e2, "kilo": 1e3, "mega": 1e6, "giga": 1e9,
}


@dataclass
class Sample:
    time_ms: int
    metrics: Dict[str, float]

    def as_dict(self) -> dict:
        return {"time": self.time_ms, "metrics": self.metrics}


@dataclass
class ParseResult:
    sample: Sample
    metadata: dict = field(default_factory=dict)

    @property
    def metric_names(self) -> List[str]:
        return sorted(self.sample.metrics)


def local_name(tag: str) -> str:
    """Strip the XML namespace: '{urn:...}node' -> 'node'."""
    if not tag:
        return ""
    return tag.split("}", 1)[1] if "}" in tag else tag


def to_float(text: Optional[str]) -> Optional[float]:
    if text is None:
        return None
    s = text.strip().replace(",", ".")
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def to_ms(ts: Optional[str]) -> Optional[int]:
    """ISO-8601 or unix time (s or ms) -> epoch milliseconds; None if unparseable."""
    if ts is None:
        return None
    s = ts.strip()
    if not s:
        return None
    n = to_float(s)
    if n is not None:
        if n <= 0:
            return None
        if n > 1e11:  # already milliseconds
            return int(n)
        if n > 1e8:  # seconds
            return int(n * 1000)
        return None
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def child_text(element: ET.Element, name: str) -> Optional[str]:
    for child in element:
        if local_name(child.tag) == name:
            return (child.text or "").strip()
    return None


def scaled_value(value: Optional[str], scale: Optional[str]) -> Optional[float]:
    number = to_float(value)
    if number is None:
        return None
    return number * SCALE_FACTORS.get((scale or "units").lower(), 1.0)


def iter_sensor_components(component: ET.Element) -> Iterator[Tuple[str, ET.Element]]:
    """Yield (name, <sensor-data>) pairs found in a <component>.

    The upstream XML sometimes omits the </component><component> boundary
    between two sensors (radon-short-term-average sits inside the pressure
    component), so walk the children in order and pair each <sensor-data>
    with the most recent <name>.
    """
    current_name = None
    for child in component:
        tag = local_name(child.tag)
        if tag == "name":
            current_name = (child.text or "").strip()
        elif tag == "sensor-data" and current_name:
            yield current_name, child


def _node_metadata(node: ET.Element, node_id: str) -> dict:
    meta = {"id": node_id, "description": child_text(node, "description"), "location": None,
            "manufacturer": None, "model": None, "serial": None, "hostname": None}
    for element in node.iter():
        tag = local_name(element.tag)
        text = (element.text or "").strip()
        if not text:
            continue
        # ietf-system <location> lives under netconf-node:config/system. The
        # NETCONF datastore list also has <location>NETCONF</location>; skip it.
        if tag == "location" and text != "NETCONF" and meta["location"] is None:
            meta["location"] = text
        elif tag == "hostname" and meta["hostname"] is None:
            meta["hostname"] = text
        elif tag == "mfg-name" and meta["manufacturer"] is None:
            meta["manufacturer"] = text
        elif tag == "model-name" and meta["model"] is None:
            meta["model"] = text
        elif tag == "serial-num" and meta["serial"] is None:
            meta["serial"] = text
    return meta


def parse_xml(xml_text: str, fallback_time_ms: Optional[int] = None) -> ParseResult:
    """Parse one data.xml document into a single sample row.

    fallback_time_ms is the archive-path timestamp (YYYY/MM/DD/HH/MM); it is
    the time of the snapshot and wins over the sensors' own value-timestamps.
    """
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise ValueError(f"cannot parse XML: {exc}") from exc

    metrics: Dict[str, float] = {}
    timestamps: List[int] = []
    nodes_meta: Dict[str, dict] = {}
    metrics_meta: Dict[str, dict] = {}

    for node in (e for e in root.iter() if local_name(e.tag) == "node"):
        node_id = child_text(node, "node-id") or "unknown-node"
        found_any = False

        for component in (e for e in node.iter() if local_name(e.tag) == "component"):
            for sensor_name, sensor_data in iter_sensor_components(component):
                scale = child_text(sensor_data, "value-scale")
                value = scaled_value(child_text(sensor_data, "value"), scale)
                if value is None:
                    continue
                if (node_id == TEMPERATURE_FIX_NODE and sensor_name == "temperature"
                        and TEMPERATURE_FIX_RANGE[0] < value < TEMPERATURE_FIX_RANGE[1]):
                    value *= 10
                key = f"{node_id}.{sensor_name}"
                metrics[key] = value
                found_any = True
                timestamp = to_ms(child_text(sensor_data, "value-timestamp"))
                if timestamp is not None:
                    timestamps.append(timestamp)
                metrics_meta[key] = {
                    "node": node_id, "sensor": sensor_name,
                    "unitsDisplay": child_text(sensor_data, "units-display"),
                    "valueType": child_text(sensor_data, "value-type"),
                    "valueScale": scale, "valueTimestamp": timestamp,
                    "updateRateMs": to_float(child_text(sensor_data, "value-update-rate")),
                }

        for thermometer in (e for e in node.iter() if local_name(e.tag) == "thermometer"):
            name = child_text(thermometer, "name")
            # lsi-thermometers report hundredths of a degree (2318 = 23.18 °C).
            value = scaled_value(child_text(thermometer, "value"), "centi")
            if name and value is not None:
                key = f"{node_id}.{name}"
                metrics[key] = value
                found_any = True
                metrics_meta[key] = {"node": node_id, "sensor": name, "unitsDisplay": "celsius",
                                     "valueType": "celsius", "valueScale": "centi",
                                     "valueTimestamp": None, "updateRateMs": None}

        if found_any:
            nodes_meta[node_id] = _node_metadata(node, node_id)

    if not metrics:
        raise ValueError("no sensor values found in data.xml")

    sample_time = fallback_time_ms or (max(timestamps) if timestamps else int(time.time() * 1000))
    return ParseResult(
        sample=Sample(sample_time, metrics),
        metadata={"nodes": nodes_meta, "metrics": metrics_meta, "sampleTime": sample_time},
    )
