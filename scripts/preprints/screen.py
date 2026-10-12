"""Auditable title/abstract triage, not an expert or full-text assessment."""
from __future__ import annotations

import re

CATEGORIES = [
    {"id": "rl-learning", "label": "RL & biological learning"},
    {"id": "ai-cognition", "label": "AI & cognitive science"},
    {"id": "ai-implications", "label": "AI with learning implications"},
]
RULE_VERSION = "2026-10-12.1"

RL = r"\breinforcement learning\b|\breward[- ](?:based )?learning\b|\breward prediction error\b|\btemporal[- ]difference (?:learning|models?|algorithms?)\b|\bmodel[- ](?:based|free) (?:learning|reinforcement|decision|control)\b|\b(?:Pavlovian|instrumental|operant) (?:learning|conditioning|tasks?)\b|\bdifferential reinforcement\b|\bsecond[- ]order conditioning\b|\bPavlovian[- ]to[- ]instrumental transfer\b|\bpolicy[- ]gradient learning\b|\bsuccessor representation\b"
BIO = r"\b(?:humans?|participants?|subjects|animals?|rats?|mice|mouse|rodents?|monkeys?|macaques?|primates?|dopamine|dopaminergic|striatal|striatum|cortico[- ]striatal)\b"
BIO_LEARN = r"\b(?:learning|learned|learn|decision[- ]making|conditioning|reward|rewards|prediction error|choice|choices|behavior|behaviour)\b"
AI = r"\b(?:artificial intelligence|machine learning|deep learning|large language models?|language models?|LLMs?|foundation models?|neural networks?|transformers?|GPT[- ]?\d*|Claude|ChatGPT|generative AI)\b"
PSY = r"\b(?:cognitive (?:science|model|models|modeling|modelling|psychology|tasks?|process|processes|abilities|architecture|maps?|testing)|human (?:cognition|reasoning|learning|memory|behavior|behaviour|decision[- ]making|decisions|variability)|primate cognition|relational reasoning|creative cognition|co[- ]creative dialogue|working memory|individual differences|psychology|psychological (?:tasks?|experiments?|theor\w*|models?|tests?|constructs?|phenomen\w*|assessments?|bias\w*|process\w*|research|science)|theory of mind|mentalizing|metacognition|cognitive bias(?:es)?|psychometric|behavioral experiment|behavioural experiment)\b"
INNOVATION = r"\b(?:introduc\w*|propos\w*|novel|new (?:task|paradigm|method|framework|benchmark|approach)|benchmark\w*|paradigm\w*|method\w*|framework\w*|assay|test\w*|experiment\w*|evaluat\w*)\b"
HUMAN_IMPACT = r"\b(?:human[- ]AI (?:collaboration|interaction|cooperation|team|learning)|human learning|human teach\w*|human tutor\w*|education(?:al)?|tutor(?:ing|s)?|student learning|human skill(?:s)?|human decision[- ]making)\b"
HUMAN_SIGNAL = r"\b(?:human feedback|human preferences?|RLHF|reinforcement learning from human feedback)\b"
CONCRETE = r"\b(?:learn\w*|teach\w*|tutor\w*|students?|participants?|experiment\w*|decision\w*|behavio\w*|collaborat\w*|interact\w*|task\w*|paradigm\w*|assay|cognit\w*|pedagog\w*)\b"
TRANSFER = r"\b(?:continual learning|lifelong learning|meta[- ]learning|in[- ]context learning|curriculum learning|catastrophic forgetting|transfer learning|compositional generalization)\b"
BRIDGE = r"\b(?:human (?:learning|cognition|memory|reasoning|adaptation)|cognitive (?:mechanism|model|architecture|process)|biological learning|brain[- ]inspired|human[- ]inspired)\b"


def hits(pattern, text):
    return list(dict.fromkeys(m.group(0) for m in re.finditer(pattern, text, flags=re.I)))


def sentences(text):
    return re.split(r"(?<=[.!?])\s+", text)


def evidence(text, patterns):
    result = []
    for sentence in sentences(text):
        if any(re.search(p, sentence, re.I) for p in patterns):
            result.append(sentence.strip()[:650])
        if len(result) >= 3:
            break
    return result


def biological_task_context(text):
    explicit = r"\bhuman (?:learning|learners?|motor learning|skill learning|volunteers?|participants?|subjects?|behavior|behaviour|decision[- ]making|decisions?|choices?|reward learning|cognition)\b"
    if re.search(explicit, text, re.I):
        return True
    for sentence in sentences(text):
        if re.search(r"\b(?:animals?|rats?|mice|mouse|rodents?|monkeys?|macaques?|primates?|zebrafish|Drosophila)\b.{0,120}\b(?:learn(?:s|ed|ing)?|choices?|conditioning|operant|Pavlovian|reward[- ]learning)\b", sentence, re.I):
            return True
        if re.search(r"\bhumans\b.{0,120}\b(?:learn(?:s|ed|ing)?|chose|choose|choices?|conditioning|operant|Pavlovian)\b", sentence, re.I):
            return True
        # Participant/subject language must describe a behavioral study. Market
        # bidders, simulated agents, hardware operators and grammatical
        # "subject to" statements do not establish biological learning.
        if re.search(r"\b(?:participants?|subjects)\b", sentence, re.I):
            artificial = r"\b(?:market|bidding|simulated|virtual|artificial|AI|LLM|agent)[- ]+participants?\b|\bparticipants? (?:in|of) (?:the )?(?:market|simulation)\b"
            if re.search(artificial, sentence, re.I):
                continue
            behavioral = r"\b(?:recruit\w*|volunteers?|behavioral experiment|behavioural experiment|asked .{0,35}(?:choose|choices)|bandit|reversal[- ]learning|decision[- ]making|reward[- ]learning|conditioning|Pavlovian|operant|learning task|choice task)\b"
            if re.search(behavioral, sentence, re.I):
                return True
        if re.search(r"\bhumans\b.{0,90}\bperform(?:ed|ing)?\b.{0,45}\b(?:bandit|reversal|decision|conditioning|learning|memory)\b.{0,15}\btask\b", sentence, re.I):
            return True
    return False


def screen(record):
    title, abstract = record.get("title", ""), record.get("abstract", "")
    text = title + ". " + abstract
    if not abstract.strip():
        return {"status": "candidate", "categories": [], "tags": [], "reason": "Abstract missing; manual assessment required.", "evidence": [], "implication": None, "rule_version": RULE_VERSION}
    groups, reasons, patterns, implications = [], [], [], []
    rl, bio, ai, psy = hits(RL, text), hits(BIO, text), hits(AI, text), hits(PSY, text)
    if rl and bio and hits(BIO_LEARN, text):
        # Human feedback used only to train an AI is scope 3, unless the study
        # actually describes biological learning/behavior or neural mechanisms.
        real_bio = bool(re.search(r"\b(?:humans?|participants?|subjects|animals?|rats?|mice|mouse|rodents?|monkeys?|macaques?|dopamin\w*|striat\w*|brain|neural circuits?)\b", text, re.I))
        biological_task = biological_task_context(text)
        neural_reward = bool(re.search(r"\b(?:dopamin\w*|striat\w*)\b", text, re.I) and re.search(r"\b(?:reward|prediction error|conditioning)\b", text, re.I))
        if real_bio and (biological_task or neural_reward):
            groups.append("rl-learning")
            reasons.append("RL/reward-learning concepts occur with human/animal behavior or learning/neural evidence.")
            patterns.extend([RL, BIO])
            implications.append("Links reward-based learning to biological behavior or neural mechanisms; relevance inferred from the abstract.")
    if ai and psy and hits(INNOVATION, text):
        groups.append("ai-cognition")
        reasons.append("AI is connected to cognitive/psychological questions and an explicit task, experiment, benchmark, or method.")
        patterns.extend([PSY, INNOVATION])
        implications.append("May contribute a task, paradigm, or method for studying cognition with AI; novelty and validity still require reading.")
    impact = hits(HUMAN_IMPACT, text)
    signal = hits(HUMAN_SIGNAL, text)
    transfer, bridge = hits(TRANSFER, text), hits(BRIDGE, text)
    concrete_impact = any(re.search(HUMAN_IMPACT, sentence, re.I) and re.search(CONCRETE, sentence, re.I)
                          for sentence in sentences(text))
    explicit_transfer = any(re.search(TRANSFER, sentence, re.I) and re.search(BRIDGE, sentence, re.I)
                            for sentence in sentences(text))
    if ai and impact and concrete_impact:
        groups.append("ai-implications")
        reasons.append("Abstract explicitly connects AI to human learning, teaching, education, decision-making, or collaboration through a concrete task or consequence.")
        patterns.append(HUMAN_IMPACT)
        implications.append("Potential relevance to how humans teach, learn from, or work with AI; not evidence that a human mechanism has been validated.")
    elif ai and transfer and bridge and explicit_transfer:
        groups.append("ai-implications")
        reasons.append("Transfer/adaptation learning is explicitly linked to humans, cognition, or learning principles.")
        patterns.extend([TRANSFER, BRIDGE])
        implications.append("Offers a possible computational comparison for learning or adaptation; transfer to human/animal learning is an interpretation, not an established result.")
    tags = list(dict.fromkeys(rl + psy + impact + signal + transfer))[:10]
    if groups:
        return {"status": "retained", "categories": groups, "tags": tags, "reason": " ".join(reasons),
                "evidence": evidence(text, patterns), "implication": " ".join(implications), "rule_version": RULE_VERSION}
    if (rl and bio) or (ai and (psy or impact or signal or transfer)):
        return {"status": "candidate", "categories": [], "tags": tags,
                "reason": "Related keywords found, but an explicit biological-learning link, cognitive method, or substantive human/learning implication was insufficient.",
                "evidence": evidence(text, [RL, PSY, HUMAN_IMPACT, TRANSFER]), "implication": None, "rule_version": RULE_VERSION}
    return {"status": "rejected", "categories": [], "tags": tags,
            "reason": "Does not meet any scope using title/abstract evidence.", "evidence": [], "implication": None, "rule_version": RULE_VERSION}
