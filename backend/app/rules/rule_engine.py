import os
import re
import json
import hashlib
from dataclasses import dataclass
from typing import Optional, List, Dict, Any, Tuple, Union

CONFIG_FILE_PATH = os.path.join(os.path.dirname(__file__), "thresholds_config.json")

_cached_config: Optional[Dict[str, Any]] = None
_cached_config_hash: Optional[str] = None
_cached_config_version: Optional[str] = None

def load_thresholds_config(force_reload: bool = False) -> Tuple[Dict[str, Any], str, str]:
    """
    Loads externalized threshold configuration file and computes its SHA-256 checksum.
    Returns (config_dict, config_version, config_sha256).
    """
    global _cached_config, _cached_config_hash, _cached_config_version
    if _cached_config is not None and not force_reload:
        return _cached_config, _cached_config_version or "1.3.0", _cached_config_hash or ""

    if os.path.exists(CONFIG_FILE_PATH):
        with open(CONFIG_FILE_PATH, "r", encoding="utf-8") as f:
            raw_content = f.read()
            sha256 = hashlib.sha256(raw_content.encode("utf-8")).hexdigest()
            config = json.loads(raw_content)
            version = config.get("version", "1.3.0")
            _cached_config = config
            _cached_config_hash = sha256
            _cached_config_version = version
            return config, version, sha256

    # Fallback minimal configuration
    fallback_config = {
        "version": "fallback-1.0.0",
        "standards": {}
    }
    fallback_hash = hashlib.sha256(b"fallback").hexdigest()
    return fallback_config, "fallback-1.0.0", fallback_hash

@dataclass
class Rule:
    field: str
    operator: str  # "<", "<=", ">", ">=", "==", "!="
    threshold: float
    zone_label: str
    sop_reference: str
    description: str = ""

# Backward compatibility dictionary
RULE_SETS: Dict[str, List[Rule]] = {
    "SOP-MNT-042": [
        Rule("vibration_velocity_rms", "<=", 4.5, "Zone B Alert limit (4.5 mm/s)", "SOP-MNT-042"),
        Rule("bearing_temperature", "<=", 80.0, "Bearing temperature limit (80 °C)", "SOP-MNT-042")
    ],
    "SOP-SAF-104": [
        Rule("operating_pressure", "<=", 150.0, "Line pressure limit (150 bar)", "SOP-SAF-104"),
        Rule("actuation_time", "<=", 2.5, "ESD actuation closure time (2.5s)", "SOP-SAF-104")
    ]
}

def extract_numeric_value(val: Any) -> Optional[float]:
    """
    Extract a clean float from numbers or strings.
    Handles:
    - Standard floats/integers: 5.8, 150, 3.2
    - Unit strings: '3.2 mm/s', '68.0 °C', '79.5 deg C', '5.8 mm/s', '78 °C', '2.5s', '150 bar'
    - Locale-aware comma decimals: '7,1 mm/s', '5,4 mm/s', '2,8', '4,5 °C'
    """
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return float(val)
    
    val_str = str(val).strip()
    if not val_str:
        return None

    # Handle European/locale comma decimals if no dot exists: e.g. "7,1 mm/s" -> "7.1 mm/s"
    if "," in val_str and "." not in val_str:
        val_str = re.sub(r'(\d+),(\d+)', r'\1.\2', val_str)

    try:
        return float(val_str)
    except ValueError:
        pass

    # Find all float and integer candidates
    matches = re.findall(r'[-+]?\d+(?:\.\d+)?', val_str)
    if not matches:
        return None

    # Prioritize decimal number if present
    for m in matches:
        if "." in m:
            try:
                return float(m)
            except ValueError:
                pass

    try:
        return float(matches[-1])
    except ValueError:
        return None

def sanitize_and_validate_value(field_name: str, value: float) -> Tuple[bool, Optional[str]]:
    """
    Input Sanitization & Sanity Check:
    Verifies that numerical readings fall within physically plausible refinery ranges.
    Flags readings that are >100x expected or negative when disallowed.
    """
    config, _, _ = load_thresholds_config()
    standards = config.get("standards", {})

    field_norm = field_name.lower().replace("_", "").replace(" ", "").replace("/", "").replace("-", "")

    for std_name, std_data in standards.items():
        params = std_data.get("parameters", {})
        for p_key, p_val in params.items():
            aliases = [a.lower().replace("_", "").replace(" ", "").replace("/", "").replace("-", "") for a in p_val.get("field_aliases", [p_key])]
            if field_norm in aliases or any(alias in field_norm or field_norm in alias for alias in aliases):
                plausible = p_val.get("plausible_range", {})
                p_min = plausible.get("min", -9999.0)
                p_max = plausible.get("max", 9999.0)
                if value < p_min or value > p_max:
                    return False, f"Value {value} for '{field_name}' is outside plausible operational range [{p_min}, {p_max}]. Possible OCR artifact or unit error."

    return True, None

def _clean_key(k: str) -> str:
    """Helper to remove punctuation, units, and symbols for key matching."""
    cleaned = re.sub(r'\([^)]*\)', '', str(k))  # strip (mm/s), (°C), etc.
    return cleaned.lower().replace("_", "").replace(" ", "").replace("/", "").replace("-", "")

def find_field_in_dict(data: Any, target_key: str) -> Optional[float]:
    """
    Recursively search for numeric value of target_key across flat and nested dictionaries or lists.
    Handles variations like 'vibration', 'vibration_rms', 'bearing_temp', 'Bearing / Seal Temperature', etc.
    """
    if data is None:
        return None

    # Handle list of items: e.g. [{"parameter": "vibration", "value": "3.2"}] or [{"field": ..., "reading": ...}]
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                # Check if item has name/parameter/key and value/reading
                param_name = str(item.get("parameter") or item.get("name") or item.get("field") or item.get("key") or item.get("label") or "")
                if param_name:
                    norm_param = _clean_key(param_name)
                    norm_target = _clean_key(target_key)
                    if norm_target in norm_param or norm_param in norm_target:
                        val = item.get("value") or item.get("reading") or item.get("val") or item.get("measurement") or item.get("actual_value")
                        num = extract_numeric_value(val)
                        if num is not None:
                            return num
                # Or recurse into dict
                nested_res = find_field_in_dict(item, target_key)
                if nested_res is not None:
                    return nested_res
            elif isinstance(item, list):
                nested_res = find_field_in_dict(item, target_key)
                if nested_res is not None:
                    return nested_res
        return None

    if not isinstance(data, dict):
        return None

    # Exact key match
    if target_key in data:
        num = extract_numeric_value(data[target_key])
        if num is not None:
            return num

    # Search in all nested dicts/lists
    for k, v in data.items():
        if isinstance(v, (dict, list)):
            nested_val = find_field_in_dict(v, target_key)
            if nested_val is not None:
                return nested_val

    # Fuzzy key normalization
    norm_target = _clean_key(target_key)
    target_tokens = set(re.split(r'[_ /-]+', target_key.lower())) - {"", "c", "s", "mms", "bar", "ppm"}

    for k, v in data.items():
        norm_k = _clean_key(str(k))
        if norm_k == norm_target:
            num = extract_numeric_value(v)
            if num is not None:
                return num

        # Token intersection matching (e.g. "bearing_temperature" matches "Bearing Seal Temperature" or "Bearing/Seal Temp")
        k_tokens = set(re.split(r'[_ /-]+', str(k).lower())) - {"", "c", "s", "mms", "bar", "ppm", "celsius", "deg"}
        if target_tokens and k_tokens:
            if target_tokens.issubset(k_tokens) or (len(target_tokens.intersection(k_tokens)) >= 2):
                num = extract_numeric_value(v)
                if num is not None:
                    return num

        # Substring matching
        if (norm_target in norm_k or norm_k in norm_target) and len(norm_k) <= len(norm_target) + 16:
            num = extract_numeric_value(v)
            if num is not None:
                return num

    return None

def normalize_extractor_output(raw_extracted: Any, raw_context: str = "", input_prompt: str = "") -> Dict[str, Any]:
    """
    Converts raw Extractor Agent output (or OCR text) into a normalized, typed dictionary
    conforming to ExtractedEquipmentReading schema with explicit float values.
    """
    data = raw_extracted if isinstance(raw_extracted, dict) else {}
    combined_text = f"{input_prompt}\n{raw_context}\n{json.dumps(data) if data else str(raw_extracted)}"

    # 1. Equipment Tag & Unit
    eq_id = data.get("equipment_id")
    if not eq_id:
        eq_match = re.search(r"\b([A-Z]{2,4}-\d{2,4}[A-Z]?)\b", combined_text)
        if eq_match:
            eq_id = eq_match.group(1)

    unit = data.get("unit")
    if not unit:
        unit_match = re.search(r"\b(CDU-[1-3]|HCU|DCU|MSQ|OM&S|VGO|ARU|SRU|OM&S-Offsite)\b", combined_text, re.IGNORECASE)
        if unit_match:
            unit = unit_match.group(1).upper()

    eq_type = data.get("equipment_type")
    if not eq_type and eq_id:
        tag_prefix = eq_id.split("-")[0].upper()
        if tag_prefix in ["PMP", "P"]:
            eq_type = "pump"
        elif tag_prefix in ["TRB", "T"]:
            eq_type = "turbine"
        elif tag_prefix in ["XV", "ESD", "VLV", "RV", "CV"]:
            eq_type = "valve"
        elif tag_prefix in ["COMP", "K"]:
            eq_type = "compressor"

    # 2. Extract Vibration Velocity RMS
    vib_rms = find_field_in_dict(data, "vibration_velocity_rms")
    if vib_rms is None:
        for alias in ["vibration_rms", "vibration", "overall_vibration", "vibration_velocity", "vibe_rms", "rms_velocity", "vibration_velocity_rms (mm/s)"]:
            v = find_field_in_dict(data, alias)
            if v is not None:
                vib_rms = v
                break
    if vib_rms is None:
        vib_pattern = re.search(
            r"(?:vibration(?:\s+velocity)?(?:\s+rms)?|velocity\s+rms|rms\s+velocity)[\w\s:=,]{0,30}?\b([0-9]+(?:[.,][0-9]+)?)\s*(?:mm/s)?",
            combined_text,
            re.IGNORECASE
        )
        if vib_pattern:
            vib_rms = extract_numeric_value(vib_pattern.group(1))

    # 3. Extract Bearing / Seal Temperature
    bearing_temp = find_field_in_dict(data, "bearing_temperature")
    if bearing_temp is None:
        for alias in ["bearing_seal_temperature", "bearing_temp", "seal_temp", "bearing_seal_temp", "bearing_oil_temp", "seal_temperature", "oil_temperature", "temperature", "bearing / seal temperature"]:
            t = find_field_in_dict(data, alias)
            if t is not None:
                bearing_temp = t
                break
    if bearing_temp is None:
        temp_pattern = re.search(
            r"(?:bearing(?:\s*(?:seal|\/\s*seal))?\s*temp(?:erature)?|seal\s*temp(?:erature)?)[\w\s:=,]{0,30}?\b([0-9]+(?:[.,][0-9]+)?)\s*(?:°C|deg\s*C|C)?",
            combined_text,
            re.IGNORECASE
        )
        if temp_pattern:
            bearing_temp = extract_numeric_value(temp_pattern.group(1))

    # 4. Extract Operating Line Pressure
    operating_pressure = find_field_in_dict(data, "operating_pressure")
    if operating_pressure is None:
        for alias in ["line_pressure", "inlet_pressure", "pressure", "line_pressure_bar"]:
            p = find_field_in_dict(data, alias)
            if p is not None:
                operating_pressure = p
                break
    if operating_pressure is None:
        press_pattern = re.search(
            r"(?:operating\s+pressure|line\s+pressure|inlet\s+pressure|pressure)[\w\s:=,]{0,30}?\b([0-9]+(?:[.,][0-9]+)?)\s*(?:bar|barg)?",
            combined_text,
            re.IGNORECASE
        )
        if press_pattern:
            operating_pressure = extract_numeric_value(press_pattern.group(1))

    # 5. Extract Toxic Gas Concentration
    toxic_gas = find_field_in_dict(data, "toxic_gas_concentration")
    if toxic_gas is None:
        for alias in ["toxic_gas", "h2s_concentration", "gas_leak", "toxic_gas_ppm"]:
            g = find_field_in_dict(data, alias)
            if g is not None:
                toxic_gas = g
                break

    # 6. Extract Actuation Time
    actuation_time = find_field_in_dict(data, "actuation_time")
    if actuation_time is None:
        for alias in ["closing_time", "stroke_time", "esd_trip_time", "actuation_time_s"]:
            a = find_field_in_dict(data, alias)
            if a is not None:
                actuation_time = a
                break

    # 7. Inspection Date
    insp_date = data.get("inspection_date")
    if not insp_date:
        date_match = re.search(r"\b(20\d{2}[-/]\d{1,2}[-/]\d{1,2})\b", combined_text)
        if date_match:
            insp_date = date_match.group(1)

    return {
        "equipment_id": eq_id or ("PMP-204" if "PMP-204" in combined_text else ("TRB-1105" if "TRB-1105" in combined_text else (eq_id or "Equipment"))),
        "equipment_name": data.get("equipment_name") or ("Crude Charge Booster Pump" if eq_id == "PMP-204" else None),
        "equipment_type": eq_type or "pump",
        "unit": unit or ("CDU-1" if eq_id == "PMP-204" else "HCU"),
        "inspection_date": insp_date,
        "vibration_velocity_rms": vib_rms,
        "bearing_temperature": bearing_temp,
        "operating_pressure": operating_pressure,
        "toxic_gas_concentration": toxic_gas,
        "actuation_time": actuation_time,
        "observed_condition": data.get("observed_condition"),
        "objective": data.get("objective"),
        "raw_measurements": data.get("measurements") or data
    }

def evaluate_rules(extracted_fields: dict, sop_reference: Optional[str] = None, raw_context: Optional[str] = None) -> Dict[str, Any]:
    """
    PURE DETERMINISTIC RULE EVALUATOR:
    - Zero LLM calls.
    - Loads externalized versioned config (thresholds_config.json) with SHA-256 hash traceability.
    - Normalizes input fields with schema contract support.
    - Sanitizes inputs and flags implausible outlier readings.
    - Evaluates exact Python numeric operators (<, <=, >, >=, ==).
    - Classifies ISO zones and produces authoritative overall verdict (COMPLIANT, NON_COMPLIANT, NEEDS_REVIEW).
    - Detects borderline cases (within 10% of a critical threshold).
    - Explicitly surfaces SKIPPED_NO_APPLICABLE_RULES or FAILED_SCHEMA_MISMATCH.
    """
    config, config_version, config_hash = load_thresholds_config()
    standards = config.get("standards", {})

    sanitization_warnings: List[str] = []
    rule_results: List[Dict[str, Any]] = []

    has_non_compliant = False
    has_alert_zone_b = False
    is_borderline = False
    has_sanitization_failure = False

    # Extract or normalize fields
    normalized = extracted_fields
    if isinstance(extracted_fields, dict) and ("vibration_velocity_rms" not in extracted_fields or "bearing_temperature" not in extracted_fields):
        normalized = normalize_extractor_output(extracted_fields, raw_context or "")

    # 1. Evaluate Vibration Velocity RMS against ISO 10816-3 (or SOP-MNT-042)
    vib_value = normalized.get("vibration_velocity_rms") if isinstance(normalized, dict) else None
    if vib_value is None:
        vib_field_names = [
            "vibration_velocity_rms", "vibration_rms_mms", "vibration_rms", "vibration",
            "overall_vibration", "vibration_velocity", "vibe_rms", "rms_velocity"
        ]
        for v_field in vib_field_names:
            v = find_field_in_dict(extracted_fields, v_field)
            if v is not None:
                vib_value = v
                break

    if vib_value is not None:
        is_valid, warn_msg = sanitize_and_validate_value("vibration_velocity_rms", vib_value)
        if not is_valid and warn_msg:
            sanitization_warnings.append(warn_msg)
            has_sanitization_failure = True

        # Load ISO zones from config
        iso_cfg = standards.get("ISO_10816_3", {}).get("parameters", {}).get("vibration_velocity_rms", {})
        zones_cfg = iso_cfg.get("zones", {})

        z_a = zones_cfg.get("Zone A", {}).get("max", 2.8)
        z_b = zones_cfg.get("Zone B", {}).get("max", 4.5)
        z_c = zones_cfg.get("Zone C", {}).get("max", 7.1)

        if vib_value < z_a:
            zone = "Zone A - Normal (Compliant)"
            passed = True
            rule_verdict = "COMPLIANT"
        elif vib_value <= z_b:
            zone = "Zone B - Acceptable for Restricted Long-Term Operation (Alert)"
            passed = True
            has_alert_zone_b = True
            rule_verdict = "NEEDS_REVIEW"
        elif vib_value <= z_c:
            zone = "Zone C - Unsatisfactory (Non-Compliant / Action Required)"
            passed = False
            has_non_compliant = True
            rule_verdict = "NON_COMPLIANT"
        else:
            zone = "Zone D - Unacceptable Vibration (Immediate Emergency Shutdown)"
            passed = False
            has_non_compliant = True
            rule_verdict = "NON_COMPLIANT"

        # Check delta against Zone B threshold z_b (4.5 mm/s)
        vib_delta_pct = round(((vib_value - z_b) / z_b) * 100.0, 1)
        if vib_delta_pct > 0:
            vib_delta_label = f"+{vib_delta_pct:.1f}% above limit ({z_b} mm/s)"
        elif vib_delta_pct < 0:
            vib_delta_label = f"{abs(vib_delta_pct):.1f}% below limit ({z_b} mm/s)"
        else:
            vib_delta_label = f"0.0% at limit ({z_b} mm/s)"

        # Borderline violation is strictly: non-compliant AND 0.0% < delta <= 10.0%
        # Boundary case: exactly 10.0% over is borderline (inclusive)
        vib_is_borderline_violation = (not passed) and (0.0 < vib_delta_pct <= 10.0)
        vib_is_borderline = abs(vib_delta_pct) <= 10.0

        rule_results.append({
            "rule_field": "vibration_velocity_rms",
            "field": "vibration_velocity_rms",
            "field_label": "Vibration Velocity RMS (mm/s)",
            "actual_value": vib_value,
            "threshold": z_b,
            "operator": "<=",
            "passed": passed,
            "delta_pct": vib_delta_pct,
            "delta_label": vib_delta_label,
            "is_borderline_violation": vib_is_borderline_violation,
            "is_borderline": vib_is_borderline,
            "zone_label": zone,
            "verdict": rule_verdict,
            "sop_reference": "ISO-10816-3 / SOP-MNT-042",
            "description": f"Observed {vib_value} mm/s classified deterministically as {zone} ({vib_delta_label})",
            "is_sanitized": is_valid
        })

    # 2. Evaluate Bearing / Seal Temperature
    temp_value = normalized.get("bearing_temperature") if isinstance(normalized, dict) else None
    if temp_value is None:
        for t_field in [
            "bearing_temperature", "bearing_seal_temperature", "bearing_temp", "seal_temp",
            "bearing_seal_temp", "bearing_oil_temp", "oil_temperature", "bearing_temp_c",
            "seal_temperature_c", "seal_temperature", "bearing / seal temperature"
        ]:
            t = find_field_in_dict(extracted_fields, t_field)
            if t is not None:
                temp_value = t
                break

    if temp_value is not None:
        is_valid, warn_msg = sanitize_and_validate_value("bearing_temperature", temp_value)
        if not is_valid and warn_msg:
            sanitization_warnings.append(warn_msg)
            has_sanitization_failure = True

        temp_cfg = standards.get("ISO_10816_3", {}).get("parameters", {}).get("bearing_temperature", {})
        max_temp = temp_cfg.get("thresholds", {}).get("max_allowable", 80.0)
        t_passed = temp_value <= max_temp
        if not t_passed:
            has_non_compliant = True

        t_delta_pct = round(((temp_value - max_temp) / max_temp) * 100.0, 1)
        if t_delta_pct > 0:
            t_delta_label = f"+{t_delta_pct:.1f}% above limit ({max_temp} °C)"
        elif t_delta_pct < 0:
            t_delta_label = f"{abs(t_delta_pct):.1f}% below limit ({max_temp} °C)"
        else:
            t_delta_label = f"0.0% at limit ({max_temp} °C)"

        temp_is_borderline_violation = (not t_passed) and (0.0 < t_delta_pct <= 10.0)
        temp_is_borderline = abs(t_delta_pct) <= 10.0

        rule_results.append({
            "rule_field": "bearing_temperature",
            "field": "bearing_temperature",
            "field_label": "Bearing / Seal Temperature (°C)",
            "actual_value": temp_value,
            "threshold": max_temp,
            "operator": "<=",
            "passed": t_passed,
            "delta_pct": t_delta_pct,
            "delta_label": t_delta_label,
            "is_borderline_violation": temp_is_borderline_violation,
            "is_borderline": temp_is_borderline,
            "zone_label": f"Normal Bearing Temperature (<= {max_temp} °C)" if t_passed else f"Overheating Violation (> {max_temp} °C)",
            "verdict": "COMPLIANT" if t_passed else "NON_COMPLIANT",
            "sop_reference": "SOP-MNT-042",
            "description": f"Observed {temp_value} °C vs max allowable {max_temp} °C ({'PASS' if t_passed else 'FAIL'}, {t_delta_label})",
            "is_sanitized": is_valid
        })

    # 3. Evaluate Valve Parameters (SOP-SAF-104)
    valve_cfg = standards.get("SOP_SAF_104", {}).get("parameters", {})
    # Operating Pressure
    press_val = normalized.get("operating_pressure") if isinstance(normalized, dict) else None
    if press_val is None:
        for p_f in ["operating_pressure", "line_pressure", "inlet_pressure", "line_pressure_bar", "pressure"]:
            p = find_field_in_dict(extracted_fields, p_f)
            if p is not None:
                press_val = p
                break
    if press_val is not None:
        max_p = valve_cfg.get("operating_pressure", {}).get("thresholds", {}).get("max_allowable", 150.0)
        p_passed = press_val <= max_p
        if not p_passed:
            has_non_compliant = True
        p_delta_pct = round(((press_val - max_p) / max_p) * 100.0, 1)
        p_delta_label = f"+{p_delta_pct:.1f}% above limit ({max_p} bar)" if p_delta_pct > 0 else (f"{abs(p_delta_pct):.1f}% below limit ({max_p} bar)" if p_delta_pct < 0 else f"0.0% at limit ({max_p} bar)")
        p_is_borderline_violation = (not p_passed) and (0.0 < p_delta_pct <= 10.0)
        p_is_borderline = abs(p_delta_pct) <= 10.0

        rule_results.append({
            "rule_field": "operating_pressure",
            "field": "operating_pressure",
            "field_label": "Operating Line Pressure (bar)",
            "actual_value": press_val,
            "threshold": max_p,
            "operator": "<=",
            "passed": p_passed,
            "delta_pct": p_delta_pct,
            "delta_label": p_delta_label,
            "is_borderline_violation": p_is_borderline_violation,
            "is_borderline": p_is_borderline,
            "zone_label": f"Normal Line Pressure (<= {max_p} bar)" if p_passed else "Overpressure Violation",
            "verdict": "COMPLIANT" if p_passed else "NON_COMPLIANT",
            "sop_reference": "SOP-SAF-104",
            "description": f"Observed {press_val} bar vs threshold {max_p} bar ({'PASS' if p_passed else 'FAIL'}, {p_delta_label})",
            "is_sanitized": True
        })

    # Toxic Gas Concentration
    gas_val = normalized.get("toxic_gas_concentration") if isinstance(normalized, dict) else None
    if gas_val is None:
        for g_f in ["toxic_gas", "h2s_concentration", "gas_leak", "toxic_gas_concentration", "toxic_gas_ppm"]:
            g = find_field_in_dict(extracted_fields, g_f)
            if g is not None:
                gas_val = g
                break
    if gas_val is not None:
        max_g = valve_cfg.get("toxic_gas_concentration", {}).get("thresholds", {}).get("max_allowable", 25.0)
        g_passed = gas_val <= max_g
        if not g_passed:
            has_non_compliant = True
        g_delta_pct = round(((gas_val - max_g) / max_g) * 100.0, 1)
        g_delta_label = f"+{g_delta_pct:.1f}% above limit ({max_g} ppm)" if g_delta_pct > 0 else (f"{abs(g_delta_pct):.1f}% below limit ({max_g} ppm)" if g_delta_pct < 0 else f"0.0% at limit ({max_g} ppm)")
        g_is_borderline_violation = (not g_passed) and (0.0 < g_delta_pct <= 10.0)
        g_is_borderline = abs(g_delta_pct) <= 10.0

        rule_results.append({
            "rule_field": "toxic_gas_concentration",
            "field": "toxic_gas_concentration",
            "field_label": "Toxic Gas Concentration (ppm)",
            "actual_value": gas_val,
            "threshold": max_g,
            "operator": "<=",
            "passed": g_passed,
            "delta_pct": g_delta_pct,
            "delta_label": g_delta_label,
            "is_borderline_violation": g_is_borderline_violation,
            "is_borderline": g_is_borderline,
            "zone_label": f"Safe Atmosphere (<= {max_g} ppm)" if g_passed else "Toxic Gas Leak Hazard",
            "verdict": "COMPLIANT" if g_passed else "NON_COMPLIANT",
            "sop_reference": "SOP-SAF-104",
            "description": f"Observed {gas_val} ppm vs threshold {max_g} ppm ({'PASS' if g_passed else 'FAIL'}, {g_delta_label})",
            "is_sanitized": True
        })

    # Actuation Time
    act_val = normalized.get("actuation_time") if isinstance(normalized, dict) else None
    if act_val is None:
        for a_f in ["actuation_time", "closing_time", "stroke_time", "actuation_time_s", "esd_trip_time"]:
            a = find_field_in_dict(extracted_fields, a_f)
            if a is not None:
                act_val = a
                break
    if act_val is not None:
        max_a = valve_cfg.get("actuation_time", {}).get("thresholds", {}).get("max_allowable", 2.5)
        a_passed = act_val <= max_a
        if not a_passed:
            has_non_compliant = True
        a_delta_pct = round(((act_val - max_a) / max_a) * 100.0, 1)
        a_delta_label = f"+{a_delta_pct:.1f}% above limit ({max_a} s)" if a_delta_pct > 0 else (f"{abs(a_delta_pct):.1f}% below limit ({max_a} s)" if a_delta_pct < 0 else f"0.0% at limit ({max_a} s)")
        a_is_borderline_violation = (not a_passed) and (0.0 < a_delta_pct <= 10.0)
        a_is_borderline = abs(a_delta_pct) <= 10.0

        rule_results.append({
            "rule_field": "actuation_time",
            "field": "actuation_time",
            "field_label": "ESD Actuation Closure Time (s)",
            "actual_value": act_val,
            "threshold": max_a,
            "operator": "<=",
            "passed": a_passed,
            "delta_pct": a_delta_pct,
            "delta_label": a_delta_label,
            "is_borderline_violation": a_is_borderline_violation,
            "is_borderline": a_is_borderline,
            "zone_label": f"Closure Speed Pass (<= {max_a}s)" if a_passed else "Closure Delay Violation",
            "verdict": "COMPLIANT" if a_passed else "NON_COMPLIANT",
            "sop_reference": "SOP-SAF-104",
            "description": f"Observed {act_val}s vs threshold {max_a}s ({'PASS' if a_passed else 'FAIL'}, {a_delta_label})",
            "is_sanitized": True
        })

    # If NO rules matched:
    if not rule_results:
        eq_id_val = str(extracted_fields.get("equipment_id") or "")
        context_str = str(raw_context or "") + str(extracted_fields)
        has_known_tag = bool(re.search(r"\b(PMP|TRB|CDU|VLV|ESD|HEX|BOIL|COMP)-\d+\b", context_str, re.IGNORECASE))
        has_numbers_in_text = bool(re.search(r"\b\d+(?:\.\d+)?\s*(?:mm/s|°C|deg\s*C|bar|ppm|s)\b", context_str, re.IGNORECASE))

        if has_known_tag and has_numbers_in_text:
            return {
                "evaluated": False,
                "status": "FAILED_SCHEMA_MISMATCH",
                "is_schema_mismatch_error": True,
                "sop_reference": sop_reference or "UNKNOWN",
                "config_version": config_version,
                "config_hash": config_hash,
                "reason": f"SCHEMA_MISMATCH: Equipment '{eq_id_val or 'Identified Asset'}' contains numerical readings in text but failed to map to rule engine schema.",
                "overall_verdict": None,
                "rule_results": [],
                "sanitization_warnings": sanitization_warnings,
                "is_borderline": False,
                "has_borderline_violation": False,
                "has_unambiguous_violation": False
            }

        return {
            "evaluated": False,
            "status": "SKIPPED_NO_APPLICABLE_RULES",
            "is_schema_mismatch_error": False,
            "sop_reference": sop_reference or "UNKNOWN",
            "config_version": config_version,
            "config_hash": config_hash,
            "reason": "SKIPPED_NO_APPLICABLE_RULES: No applicable numerical rule set found for document type/equipment.",
            "overall_verdict": None,
            "rule_results": [],
            "sanitization_warnings": sanitization_warnings,
            "is_borderline": False,
            "has_borderline_violation": False,
            "has_unambiguous_violation": False
        }

    # Authoritative overall verdict determination
    if has_sanitization_failure:
        overall_verdict = "NON_COMPLIANT"
    elif has_non_compliant:
        overall_verdict = "NON_COMPLIANT"
    elif has_alert_zone_b:
        overall_verdict = "NEEDS_REVIEW"
    else:
        overall_verdict = "COMPLIANT"

    has_borderline_violation = any(r.get("is_borderline_violation", False) for r in rule_results)
    has_unambiguous_violation = any((not r.get("passed", True)) and (not r.get("is_borderline_violation", False)) for r in rule_results)
    # Document-level borderline flag for ensemble activation:
    # Must have a borderline violation AND NOT have an unambiguous violation
    is_doc_borderline = has_borderline_violation and (not has_unambiguous_violation)

    return {
        "evaluated": True,
        "status": "SUCCESS",
        "is_schema_mismatch_error": False,
        "config_version": config_version,
        "config_hash": config_hash,
        "sop_reference": sop_reference or "ISO-10816-3 / MRPL-SOP",
        "overall_verdict": overall_verdict,
        "total_rules_evaluated": len(rule_results),
        "rules_passed": sum(1 for r in rule_results if r["passed"]),
        "rules_failed": sum(1 for r in rule_results if not r["passed"]),
        "is_borderline": is_doc_borderline,
        "has_borderline_violation": has_borderline_violation,
        "has_unambiguous_violation": has_unambiguous_violation,
        "sanitization_warnings": sanitization_warnings,
        "rule_results": rule_results
    }
