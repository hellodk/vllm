"""
Hallucination detection algorithms and text analysis utilities.

This module provides functions to detect potential hallucination signals:
- Entropy analysis: High entropy suggests uncertainty
- Repetition detection: Looping/repetitive text patterns
- Perplexity calculation: Model's uncertainty about its output
- Refusal detection: Identifying "I don't know" style responses
- Confidence analysis: Statistical analysis of token probabilities
"""

import math
import re
from typing import List, Tuple, Optional
from collections import Counter


def compute_entropy(probabilities: List[float], epsilon: float = 1e-10) -> float:
    """
    Compute Shannon entropy of token probability distribution.
    
    Higher entropy indicates more uncertainty in the model's predictions,
    which can be a signal of potential hallucination.
    
    Args:
        probabilities: List of token probabilities
        epsilon: Small value to prevent log(0)
    
    Returns:
        Entropy value (in nats). Typical range: 0-5 for LLM outputs.
        - 0-1: Very confident
        - 1-2: Normal confidence
        - 2-3: Moderate uncertainty
        - 3-4: High uncertainty
        - 4+: Very uncertain (potential hallucination)
    """
    if not probabilities:
        return 0.0
    
    entropy = 0.0
    for p in probabilities:
        if p > epsilon:
            entropy -= p * math.log(p + epsilon)
    
    return entropy


def compute_perplexity(probabilities: List[float], epsilon: float = 1e-10) -> float:
    """
    Compute perplexity from token probabilities.
    
    Perplexity = exp(average negative log probability)
    Lower perplexity indicates the model is more confident.
    
    Args:
        probabilities: List of token probabilities
        epsilon: Small value to prevent log(0)
    
    Returns:
        Perplexity value. Typical range: 1-100 for normal outputs.
        - 1-10: Very confident
        - 10-30: Normal
        - 30-50: Moderate uncertainty
        - 50+: High uncertainty
    """
    if not probabilities:
        return 1.0
    
    log_probs = [math.log(p + epsilon) for p in probabilities]
    avg_log_prob = sum(log_probs) / len(log_probs)
    
    return math.exp(-avg_log_prob)


def compute_confidence_stats(probabilities: List[float]) -> Tuple[float, float]:
    """
    Compute mean and standard deviation of token confidences.
    
    Args:
        probabilities: List of token probabilities
    
    Returns:
        Tuple of (mean, std_dev) confidence values
    """
    if not probabilities:
        return 0.0, 0.0
    
    n = len(probabilities)
    mean = sum(probabilities) / n
    
    if n < 2:
        return mean, 0.0
    
    variance = sum((p - mean) ** 2 for p in probabilities) / (n - 1)
    std_dev = math.sqrt(variance)
    
    return mean, std_dev


def compute_repetition_score(
    text: str,
    n: int = 3,
    word_level: bool = True,
) -> float:
    """
    Compute n-gram repetition score to detect looping/repetitive text.
    
    Repetitive text (e.g., "the the the..." or repeating phrases) is
    a common hallucination pattern.
    
    Args:
        text: Text to analyze
        n: N-gram size (3 = trigrams by default)
        word_level: If True, use word n-grams; if False, use character n-grams
    
    Returns:
        Repetition score from 0.0 (no repetition) to 1.0 (all repeated).
        - 0.0-0.1: Normal text
        - 0.1-0.2: Some repetition (might be ok)
        - 0.2-0.3: Moderate repetition (potential issue)
        - 0.3+: High repetition (likely hallucination)
    """
    if not text or len(text.strip()) == 0:
        return 0.0
    
    if word_level:
        # Tokenize into words
        words = text.lower().split()
        if len(words) < n:
            return 0.0
        
        # Generate n-grams
        ngrams = [tuple(words[i:i+n]) for i in range(len(words) - n + 1)]
    else:
        # Character-level n-grams
        text = text.lower()
        if len(text) < n:
            return 0.0
        
        ngrams = [text[i:i+n] for i in range(len(text) - n + 1)]
    
    if not ngrams:
        return 0.0
    
    # Count n-grams
    counts = Counter(ngrams)
    
    # Calculate repetition: sum of (count - 1) for all repeated n-grams
    repeated = sum(count - 1 for count in counts.values() if count > 1)
    
    # Normalize by total n-grams
    return repeated / len(ngrams)


def detect_consecutive_repetition(text: str, min_repeat: int = 3) -> bool:
    """
    Detect consecutive word repetition (e.g., "the the the").
    
    Args:
        text: Text to analyze
        min_repeat: Minimum consecutive repetitions to trigger
    
    Returns:
        True if consecutive repetition detected
    """
    words = text.lower().split()
    if len(words) < min_repeat:
        return False
    
    consecutive = 1
    for i in range(1, len(words)):
        if words[i] == words[i-1]:
            consecutive += 1
            if consecutive >= min_repeat:
                return True
        else:
            consecutive = 1
    
    return False


def detect_refusal(text: str) -> bool:
    """
    Detect if the response is a refusal/uncertainty response.
    
    These patterns indicate the model is declining to answer or
    expressing uncertainty about its knowledge.
    
    Args:
        text: Response text to analyze
    
    Returns:
        True if refusal patterns detected
    """
    refusal_patterns = [
        r"i (?:don'?t|do not|cannot|can'?t) (?:know|answer|provide|help)",
        r"i'?m (?:not sure|uncertain|unable)",
        r"(?:sorry|apologies),? (?:but )?i (?:can'?t|cannot|don'?t)",
        r"i (?:don'?t|do not) have (?:enough )?(?:information|knowledge|data)",
        r"(?:as an ai|as a language model),? i (?:can'?t|cannot|don'?t)",
        r"i'?m (?:just )?(?:an? )?(?:ai|language model|llm)",
        r"i (?:would )?(?:need|require) more (?:information|context|details)",
        r"(?:that'?s|this is) (?:beyond|outside) my (?:knowledge|capabilities)",
        r"i'?m not (?:able|capable|qualified) to",
        r"(?:unfortunately|regrettably),? i (?:can'?t|cannot)",
    ]
    
    text_lower = text.lower()
    
    for pattern in refusal_patterns:
        if re.search(pattern, text_lower):
            return True
    
    return False


def detect_hedging(text: str) -> float:
    """
    Detect hedging language that indicates uncertainty.
    
    Returns a score from 0.0 to 1.0 indicating the degree of hedging.
    
    Args:
        text: Response text to analyze
    
    Returns:
        Hedging score (0.0 = no hedging, 1.0 = heavy hedging)
    """
    hedging_words = [
        "maybe", "perhaps", "possibly", "probably", "might", "could",
        "seems", "appears", "likely", "unlikely", "uncertain", "unclear",
        "approximately", "roughly", "around", "about", "estimate",
        "i think", "i believe", "i suppose", "in my opinion",
        "it's possible", "it seems", "it appears", "it might be",
    ]
    
    text_lower = text.lower()
    word_count = len(text_lower.split())
    
    if word_count == 0:
        return 0.0
    
    hedge_count = sum(1 for phrase in hedging_words if phrase in text_lower)
    
    # Normalize by word count (longer text naturally has more hedging words)
    normalized_score = hedge_count / (word_count / 100)  # Per 100 words
    
    # Cap at 1.0
    return min(1.0, normalized_score / 5)  # 5 hedges per 100 words = 1.0


def compute_hallucination_risk_score(
    entropy: float,
    repetition: float,
    perplexity: float,
    confidence_mean: float,
    is_refusal: bool,
) -> float:
    """
    Compute a composite hallucination risk score.
    
    Combines multiple signals into a single score from 0.0 (low risk)
    to 1.0 (high risk).
    
    Args:
        entropy: Output entropy
        repetition: Repetition score
        perplexity: Output perplexity
        confidence_mean: Mean token confidence
        is_refusal: Whether response is a refusal
    
    Returns:
        Risk score from 0.0 to 1.0
    """
    # Weights for each factor
    weights = {
        "entropy": 0.25,
        "repetition": 0.25,
        "perplexity": 0.20,
        "confidence": 0.20,
        "refusal": 0.10,
    }
    
    score = 0.0
    
    # Entropy: normalize 0-5 to 0-1 risk
    entropy_risk = min(1.0, entropy / 5.0)
    score += weights["entropy"] * entropy_risk
    
    # Repetition: already 0-1
    score += weights["repetition"] * repetition
    
    # Perplexity: normalize 1-100 to 0-1 risk
    perplexity_risk = min(1.0, (perplexity - 1) / 99)
    score += weights["perplexity"] * perplexity_risk
    
    # Low confidence: invert mean confidence to get risk
    confidence_risk = 1.0 - confidence_mean
    score += weights["confidence"] * confidence_risk
    
    # Refusal: binary contribution
    score += weights["refusal"] * (1.0 if is_refusal else 0.0)
    
    return score


def analyze_text_quality(text: str, token_probs: Optional[List[float]] = None) -> dict:
    """
    Perform comprehensive text quality analysis.
    
    Args:
        text: Generated text
        token_probs: Optional token probabilities
    
    Returns:
        Dictionary with quality metrics
    """
    results = {
        "repetition_score": compute_repetition_score(text),
        "repetition_char": compute_repetition_score(text, n=5, word_level=False),
        "consecutive_repetition": detect_consecutive_repetition(text),
        "is_refusal": detect_refusal(text),
        "hedging_score": detect_hedging(text),
        "word_count": len(text.split()),
        "char_count": len(text),
    }
    
    if token_probs:
        results["entropy"] = compute_entropy(token_probs)
        results["perplexity"] = compute_perplexity(token_probs)
        conf_mean, conf_std = compute_confidence_stats(token_probs)
        results["confidence_mean"] = conf_mean
        results["confidence_std"] = conf_std
        
        # Compute composite risk score
        results["hallucination_risk"] = compute_hallucination_risk_score(
            entropy=results["entropy"],
            repetition=results["repetition_score"],
            perplexity=results["perplexity"],
            confidence_mean=conf_mean,
            is_refusal=results["is_refusal"],
        )
    
    return results
