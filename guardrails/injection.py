"""
CreditMind - Prompt-injection detection for untrusted borrower text.

Borrower-written fields (loan description, job title, loan title) are DATA, never
instructions. This detector flags text that tries to instruct or manipulate the AI.

Design:
  * Text is normalised first (Unicode tricks, zero-width characters, spacing)
  * Each pattern has a severity weight; detected when the top weight >= 0.7
  * Polite requests like "please approve my loan" are common in real applications,
    so on their own they only count as low severity (avoids false positives)
"""

import re
import unicodedata
from dataclasses import dataclass, field

DETECTION_THRESHOLD = 0.7

PATTERNS = [
    # (name, weight, regex)
    ("ignore_instructions", 0.95,
     r"\b(ignore|disregard|forget|override|bypass|skip)\b.{0,40}"
     r"\b(instructions?|rules?|polic(y|ies)|guidelines?|prompts?|checks?|guardrails?)\b"),
    ("role_override", 0.9,
     r"\b(you are now|act as|pretend (to be|you are)|from now on,? you|roleplay as)\b"),
    ("reveal_prompt", 0.9,
     r"\b(system prompt|reveal .{0,25}(prompt|instructions)|your (hidden )?instructions)\b"),
    ("fake_role_tag", 0.9,
     r"(^|\n|\s)(system|assistant|developer)\s*:|<\s*/?\s*(system|instructions?|prompt)\s*>"),
    ("manipulate_output", 0.85,
     r"\b(set|change|mark|output|return|label)\b.{0,30}\b(decision|risk|score|status|outcome)\b"
     r".{0,30}\b(approve|approved|low|zero|0)\b"),
    ("directed_approval", 0.8,
     r"\b(you (must|will|should|have to|are required to)|ai|model|system|assistant)\b"
     r".{0,40}\bapprove\b"),
    ("jailbreak_terms", 0.9, r"\b(jailbreak|dan mode|developer mode|prompt injection)\b"),
    ("authority_claim", 0.75,
     r"\b(authori[sz]ed (override|test|exception)|admin(istrator)? (override|command))\b"),
    ("polite_request", 0.3, r"\bplease\b.{0,20}\bapprove\b"),
]
COMPILED = [(n, w, re.compile(p, re.IGNORECASE | re.DOTALL)) for n, w, p in PATTERNS]

ZERO_WIDTH = dict.fromkeys(map(ord, "\u200b\u200c\u200d\u2060\ufeff"), None)


@dataclass
class InjectionResult:
    detected: bool = False
    score: float = 0.0
    matches: list = field(default_factory=list)  # pattern names only, never the text


def normalise(text):
    text = unicodedata.normalize("NFKC", text).translate(ZERO_WIDTH)
    return re.sub(r"\s+", " ", text).strip()


def detect_injection(text):
    if not text:
        return InjectionResult()
    clean = normalise(text)
    matches = [(name, weight) for name, weight, rx in COMPILED if rx.search(clean)]
    score = max((w for _, w in matches), default=0.0)
    if len(matches) >= 2:  # several weaker signals together are suspicious
        score = min(1.0, score + 0.1)
    return InjectionResult(detected=score >= DETECTION_THRESHOLD, score=round(score, 2),
                           matches=[name for name, _ in matches])