"""
CreditMind - PII detection and masking (Microsoft Presidio + custom Indian recognizers).

Two layers:
  1. Known values: the applicant's own name/phone/email/PAN are masked exactly
  2. Presidio: NER (names) + patterns (email, phone, PAN, Aadhaar, card numbers)
     catch any OTHER personal data typed into free text

Setup (one time):
    pip install presidio-analyzer presidio-anonymizer spacy
    python -m spacy download en_core_web_sm
"""

import re
from functools import lru_cache

ENTITIES = ["PERSON", "EMAIL_ADDRESS", "PHONE_NUMBER", "CREDIT_CARD", "IBAN_CODE",
            "US_SSN", "IP_ADDRESS", "PAN_NUMBER", "AADHAAR_NUMBER", "IN_PHONE"]

# Regexes also reused by the output guardrail to detect leaks
PAN_RE = r"\b[A-Z]{5}[0-9]{4}[A-Z]\b"
AADHAAR_RE = r"\b[2-9][0-9]{3}[\s-]?[0-9]{4}[\s-]?[0-9]{4}\b"
IN_PHONE_RE = r"(?:\+91[\s-]?)?\b[6-9][0-9]{9}\b"
EMAIL_RE = r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b"


@lru_cache(maxsize=1)
def _engines():
    """Load Presidio once (slow to start, fast afterwards)."""
    from presidio_analyzer import AnalyzerEngine, Pattern, PatternRecognizer
    from presidio_analyzer.nlp_engine import NlpEngineProvider
    from presidio_anonymizer import AnonymizerEngine

    nlp = NlpEngineProvider(nlp_configuration={
        "nlp_engine_name": "spacy",
        "models": [{"lang_code": "en", "model_name": "en_core_web_sm"}],
    }).create_engine()
    analyzer = AnalyzerEngine(nlp_engine=nlp, supported_languages=["en"])
    analyzer.registry.add_recognizer(PatternRecognizer(
        supported_entity="PAN_NUMBER", context=["pan", "permanent account"],
        patterns=[Pattern("pan", PAN_RE, 0.9)]))
    analyzer.registry.add_recognizer(PatternRecognizer(
        supported_entity="AADHAAR_NUMBER", context=["aadhaar", "aadhar", "uid"],
        patterns=[Pattern("aadhaar", AADHAAR_RE, 0.7)]))
    analyzer.registry.add_recognizer(PatternRecognizer(
        supported_entity="IN_PHONE", context=["phone", "mobile", "call", "contact"],
        patterns=[Pattern("in_phone", IN_PHONE_RE, 0.75)]))
    return analyzer, AnonymizerEngine()


def _mask_known_values(text, known):
    """Mask the applicant's own details exactly, even if NER misses them."""
    labels = {"applicant_name": "<APPLICANT_NAME>", "phone": "<PHONE>",
              "email": "<EMAIL>", "pan": "<PAN>"}
    found = []
    for field, label in labels.items():
        value = known.get(field)
        if not value:
            continue
        variants = [value]
        if field == "applicant_name":
            variants += [part for part in value.split() if len(part) >= 3]
        if field == "phone":
            variants.append(re.sub(r"\D", "", value)[-10:])  # bare 10-digit form
        for variant in sorted(set(variants), key=len, reverse=True):
            pattern = re.compile(rf"(?<!\w){re.escape(variant)}(?!\w)", re.IGNORECASE)
            if pattern.search(text):
                text = pattern.sub(label, text)
                found.append(label.strip("<>"))
    return text, found


def mask_pii(text, known=None, score_threshold=0.5, entities=None):
    """Return (masked_text, list of entity types found). Values are never returned."""
    if not text:
        return text, []
    text, found = _mask_known_values(text, known or {})
    analyzer, anonymizer = _engines()
    results = analyzer.analyze(text=text, language="en", entities=entities or ENTITIES,
                               score_threshold=score_threshold)
    # Don't re-mask our own placeholders
    results = [r for r in results if not text[r.start:r.end].startswith("<")]
    if results:
        text = anonymizer.anonymize(text=text, analyzer_results=results).text
        found += [r.entity_type for r in results]
    return text, sorted(set(found))


def find_pii_leaks(text, known=None):
    """Used on LLM OUTPUT: returns descriptions of any personal data present."""
    leaks = []
    for field, value in (known or {}).items():
        if value and re.search(rf"(?<!\w){re.escape(str(value))}(?!\w)", text, re.IGNORECASE):
            leaks.append(f"applicant {field}")
    for name, regex in [("email address", EMAIL_RE), ("PAN", PAN_RE),
                        ("Aadhaar number", AADHAAR_RE), ("phone number", IN_PHONE_RE)]:
        if re.search(regex, text):
            leaks.append(name)
    return sorted(set(leaks))