#!/usr/bin/env python3
"""
LogWhisperer Summarizer - AI-powered log summarization module
Production-ready version with caching, error handling, and performance optimizations.
"""

import requests
import json
import time
import hashlib
import logging
from typing import List, Dict, Any, Optional, Tuple, Union, Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from functools import lru_cache
from threading import Lock
import re
from collections import Counter as CollectionsCounter

# Configure logging
logger = logging.getLogger(__name__)

# Constants
DEFAULT_MODEL = "mistral"
DEFAULT_HOST = "http://localhost:11434"
DEFAULT_TIMEOUT = 60
DEFAULT_LINES_PER_PROMPT = 50
MAX_PROMPT_LENGTH = 32000  # Safe limit for most models
MAX_RETRIES = 3
CACHE_TTL = 300  # 5 minutes

# Module-level variables for singleton
_summarizer: Optional['OllamaSummarizer'] = None
_summarizer_lock = Lock()


@dataclass
class SummaryRequest:
    """Structured summary request."""
    lines: List[str]
    model: str = DEFAULT_MODEL
    host: str = DEFAULT_HOST
    timeout: int = DEFAULT_TIMEOUT
    prompt_template: Optional[str] = None
    context: Optional[Dict[str, Any]] = None

    def cache_key(self) -> str:
        """Generate cache key for this request."""
        # Include first/last few lines and count for efficiency
        line_sample = []
        if self.lines:
            line_sample.extend(self.lines[:5])  # First 5
            line_sample.extend(self.lines[-5:])  # Last 5
            line_sample.append(f"__count:{len(self.lines)}")

        content = json.dumps(
            {
                "lines": line_sample,
                "model": self.model,
                "template": self.prompt_template,
                "context": self.context,
            },
            sort_keys=True,
        )

        return hashlib.sha256(content.encode()).hexdigest()


@dataclass
class SummaryResponse:
    """Structured summary response."""
    summary: str
    model: str
    processing_time: float
    line_count: int
    cached: bool = False
    error: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = field(default_factory=dict)


class SummaryCache:
    """Thread-safe cache for summaries."""

    def __init__(self, ttl: int = CACHE_TTL):
        self._cache: Dict[str, Tuple[SummaryResponse, datetime]] = {}
        self._lock = Lock()
        self.ttl = ttl
        self.hits = 0
        self.misses = 0

    def get(self, key: str) -> Optional[SummaryResponse]:
        """Get cached summary if valid."""
        with self._lock:
            if key in self._cache:
                response, timestamp = self._cache[key]
                if datetime.now() - timestamp < timedelta(seconds=self.ttl):
                    self.hits += 1
                    # Create a copy to avoid modifying cached object
                    cached_response = SummaryResponse(
                        summary=response.summary,
                        model=response.model,
                        processing_time=response.processing_time,
                        line_count=response.line_count,
                        cached=True,
                        error=response.error,
                        metadata=response.metadata.copy() if response.metadata else {}
                    )
                    return cached_response
                else:
                    # Expired
                    del self._cache[key]

            self.misses += 1
            return None

    def set(self, key: str, response: SummaryResponse) -> None:
        """Cache a summary response."""
        with self._lock:
            self._cache[key] = (response, datetime.now())
            # Cleanup old entries
            self._cleanup()

    def _cleanup(self) -> None:
        """Remove expired entries."""
        now = datetime.now()
        expired = [
            key
            for key, (_, timestamp) in self._cache.items()
            if now - timestamp > timedelta(seconds=self.ttl)
        ]
        for key in expired:
            del self._cache[key]

    def stats(self) -> Dict[str, Union[int, float]]:
        """Get cache statistics."""
        with self._lock:
            total_requests = self.hits + self.misses
            return {
                "size": len(self._cache),
                "hits": self.hits,
                "misses": self.misses,
                "hit_rate": (
                    self.hits / total_requests
                    if total_requests > 0
                    else 0.0
                ),
            }


class LogAnalyzer:
    """Analyze logs before summarization."""

    @staticmethod
    def analyze_logs(lines: List[str]) -> Dict[str, Any]:
        """Analyze log lines for patterns and statistics."""
        if not lines:
            return {}

        analysis: Dict[str, Any] = {
            "total_lines": len(lines),
            "unique_lines": len(set(lines)),
            "error_keywords": 0,
            "warning_keywords": 0,
            "common_patterns": [],
            "time_range": None,
            "sources": [],
        }

        # Keyword counters
        error_pattern = re.compile(
            r"\b(error|fail|failed|exception|critical|fatal)\b", re.IGNORECASE
        )
        warning_pattern = re.compile(r"\b(warn|warning|caution|alert)\b", re.IGNORECASE)

        # Pattern extraction
        ip_pattern = re.compile(r"\b(?:[0-9]{1,3}\.){3}[0-9]{1,3}\b")
        timestamp_pattern = re.compile(r"\d{4}-\d{2}-\d{2}[T\s]\d{2}:\d{2}:\d{2}")

        timestamps: List[str] = []
        ip_addresses: List[str] = []

        for line in lines:
            # Count keywords
            if error_pattern.search(line):
                analysis["error_keywords"] += 1
            if warning_pattern.search(line):
                analysis["warning_keywords"] += 1

            # Extract IPs
            ips = ip_pattern.findall(line)
            ip_addresses.extend(ips)

            # Extract timestamps
            ts_matches = timestamp_pattern.findall(line)
            timestamps.extend(ts_matches)

        # Find common IPs
        if ip_addresses:
            ip_counter = Counter(ip_addresses)
            analysis["common_ips"] = ip_counter.most_common(5)

        # Calculate time range
        if timestamps:
            try:
                parsed_times: List[datetime] = []
                for ts in timestamps[:100]:  # Limit for performance
                    try:
                        parsed_times.append(
                            datetime.fromisoformat(ts.replace(" ", "T"))
                        )
                    except:
                        pass

                if parsed_times:
                    analysis["time_range"] = {
                        "start": min(parsed_times).isoformat(),
                        "end": max(parsed_times).isoformat(),
                        "duration": str(max(parsed_times) - min(parsed_times)),
                    }
            except: Exception
            pass

        # Detect common patterns
        pattern_counter: Counter[str] = CollectionsCounter()
        for line in lines[:1000]:  # Sample for performance
            # Remove numbers and timestamps to find patterns
            cleaned = re.sub(r"\d+", "N", line)
            cleaned = re.sub(r"\b[A-Fa-f0-9]{8,}\b", "HEX", cleaned)
            pattern_counter[cleaned] += 1

        # Get most common patterns - now mypy knows it's a Counter
        analysis["common_patterns"] = [
            {
                "pattern": pattern[:100] + "..." if len(pattern) > 100 else pattern,
                "count": count,
            }
            for pattern, count in pattern_counter.most_common(5)
            if count > 1
        ]

        return analysis


class OllamaSummarizer:
    """Enhanced Ollama-based log summarizer."""

    def __init__(
        self,
        host: str = DEFAULT_HOST,
        model: str = DEFAULT_MODEL,
        timeout: int = DEFAULT_TIMEOUT,
        cache_ttl: int = CACHE_TTL,
    ):
        """Initialize the summarizer."""
        self.host = host.rstrip("/")
        self.model = model
        self.timeout = timeout
        self.cache = SummaryCache(ttl=cache_ttl)
        self._session = requests.Session()
        self._session.headers.update(
            {"User-Agent": "LogWhisperer/1.0", "Content-Type": "application/json"}
        )

        logger.info(f"Initialized OllamaSummarizer with model: {model}, host: {host}")

    def ensure_model_available(self) -> bool:
        """Check if model is available."""
        try:
            response = self._session.post(
                f"{self.host}/api/show",
                json={"name": self.model},
                timeout=10
            )
            return response.status_code == 200
        except Exception as e:
            logger.warning(f"Failed to check model availability: {e}")
            return False

    def summarize(
        self,
        lines: List[str],
        prompt_template: Optional[str] = None,
        use_cache: bool = True,
        analyze_first: bool = True,
    ) -> SummaryResponse:
        """
        Summarize log lines with optional analysis.
        """
        start_time = time.time()
        
        # Preprocess logs first
        if len(lines) > 100:  # Only preprocess if we have many lines
            lines = self._preprocess_logs(lines)

        # Create request object
        request = SummaryRequest(
            lines=lines,
            model=self.model,
            host=self.host,
            timeout=self.timeout,
            prompt_template=prompt_template,
        )

        # Check cache
        cache_key: Optional[str] = None
        if use_cache:
            cache_key = request.cache_key()
            cached = self.cache.get(cache_key)
            if cached:
                logger.debug(f"Cache hit for {cache_key[:8]}...")
                return cached

        # Analyze logs if requested
        metadata: Dict[str, Any] = {}
        if analyze_first and lines:
            try:
                metadata = LogAnalyzer.analyze_logs(lines)
                logger.debug(
                    f"Log analysis complete: {metadata.get('total_lines')} lines"
                )
            except Exception as e:
                logger.warning(f"Log analysis failed: {e}")

        # Build prompt
        prompt = self._build_prompt(lines, prompt_template, metadata)

        # Get summary from Ollama
        try:
            # Use streaming for large prompts
            if len(prompt) > 10000:
                summary = self._call_ollama_streaming(prompt, self.model)
            else:
                summary = self._call_ollama(prompt, self.model)

            response = SummaryResponse(
                summary=summary,
                model=self.model,
                processing_time=time.time() - start_time,
                line_count=len(lines),
                cached=False,
                metadata=metadata,
            )

            # Cache successful response
            if use_cache and cache_key is not None:
                self.cache.set(cache_key, response)

            return response

        except Exception as e:
            logger.error(f"Summarization failed: {e}")
            return SummaryResponse(
                summary=f"[Error: Could not generate summary - {str(e)}]",
                model=self.model,
                processing_time=time.time() - start_time,
                line_count=len(lines),
                cached=False,
                error=str(e),
                metadata=metadata,
            )

    def _build_prompt(
        self, lines: List[str], template: Optional[str], metadata: Dict[str, Any]
    ) -> str:
        """Build prompt for the LLM."""
        if not lines:
            return "No logs to summarize."

        # Prepare log content
        log_content = "\n".join(lines)

        # Truncate if too long
        if len(log_content) > MAX_PROMPT_LENGTH:
            logger.warning(
                f"Truncating logs from {len(log_content)} to {MAX_PROMPT_LENGTH} chars"
            )
            log_content = log_content[:MAX_PROMPT_LENGTH] + "\n... [truncated]"

        # Use custom template if provided
        if template:
            # Handle {{LOGS}} placeholder (compatibility with main script)
            if "{{LOGS}}" in template:
                prompt = template.replace("{{LOGS}}", log_content)
            else:
                prompt = template.replace("{{LOGS}}", log_content) if "{{LOGS}}" in template else template

            # Replace metadata placeholders if present
            if metadata:
                prompt = prompt.replace(
                    "{{TOTAL_LINES}}", str(metadata.get("total_lines", len(lines)))
                )
                prompt = prompt.replace(
                    "{{ERROR_COUNT}}", str(metadata.get("error_keywords", 0))
                )
                prompt = prompt.replace(
                    "{{WARNING_COUNT}}", str(metadata.get("warning_keywords", 0))
                )
        else:
            # Build default prompt with metadata context
            prompt = "You are an expert Linux system administrator and log analyst. "

            # Add context from metadata
            if metadata:
                if metadata.get("error_keywords", 0) > 0:
                    prompt += (
                        f"Note: Found {metadata['error_keywords']} error indicators. "
                    )
                if metadata.get("warning_keywords", 0) > 0:
                    prompt += (
                        f"Found {metadata['warning_keywords']} warning indicators. "
                    )
                if metadata.get("time_range"):
                    prompt += f"Logs span {metadata['time_range']['duration']}. "

            prompt += "\n\nAnalyze the following system logs and provide:\n"
            prompt += "1. A concise summary of the main issues or events\n"
            prompt += "2. Identification of any critical errors or problems\n"
            prompt += "3. Root cause analysis where possible\n"
            prompt += "4. Specific, actionable recommendations\n\n"
            prompt += "Focus on the most important findings and be concise.\n\n"
            prompt += f"LOGS:\n{log_content}"

        return prompt

    def _call_ollama(self, prompt: str, model: str) -> str:
        """Call Ollama API with enhanced retry logic and timeout handling."""
        retry_count = 0
        last_error: Optional[str] = None
        
        # Dynamic timeout based on prompt size
        base_timeout = self.timeout
        prompt_size_factor = len(prompt) / 10000  # Adjust timeout based on prompt size
        dynamic_timeout = int(base_timeout * (1 + prompt_size_factor))
        dynamic_timeout = min(dynamic_timeout, 300)  # Cap at 5 minutes

        while retry_count < MAX_RETRIES:
            try:
                logger.debug(f"Attempt {retry_count + 1}, timeout: {dynamic_timeout}s")
                
                response = self._session.post(
                    f"{self.host}/api/generate",
                    json={
                        "model": model,
                        "prompt": prompt,
                        "stream": False,
                        "options": {
                            "temperature": 0.3,
                            "top_p": 0.9,
                            "num_predict": 1000,
                            "num_ctx": 4096,  # Increase context window
                        },
                    },
                    timeout=dynamic_timeout,
                )

                response.raise_for_status()
                result = response.json()
                summary = result.get("response", "").strip()

                if not summary:
                    raise ValueError("Empty response from Ollama")

                return summary

            except requests.exceptions.Timeout:
                last_error = f"Request timed out after {dynamic_timeout}s"
                retry_count += 1

                if retry_count < MAX_RETRIES:
                    # Exponential backoff
                    wait_time = min(retry_count * 5, 30)
                    logger.warning(f"Timeout, retrying in {wait_time}s...")
                    time.sleep(wait_time)
                    
                    # Try with smaller prompt on retry
                    if len(prompt) > 5000:
                        prompt = prompt[:5000] + "\n\n[Content truncated due to timeout]"

            except requests.exceptions.RequestException as e:
                last_error = f"Network error: {e}"
                retry_count += 1

                if retry_count < MAX_RETRIES:
                    logger.warning(f"Request failed, retry {retry_count}/{MAX_RETRIES}")
                    time.sleep(2 * retry_count)

            except Exception as e:
                last_error = f"Unexpected error: {e}"
                break

        raise RuntimeError(f"Failed after {retry_count} attempts: {last_error}")

    def _call_ollama_streaming(self, prompt: str, model: str) -> str:
        """Call Ollama API with streaming to avoid timeouts."""
        try:
            response = self._session.post(
                f"{self.host}/api/generate",
                json={
                    "model": model,
                    "prompt": prompt,
                    "stream": True,  # Enable streaming
                    "options": {
                        "temperature": 0.3,
                        "top_p": 0.9,
                        "num_predict": 1000,
                    },
                },
                stream=True,
                timeout=30,  # Shorter timeout for initial connection
            )

            response.raise_for_status()
            
            full_response = []
            last_activity = time.time()
            
            for line in response.iter_lines():
                if line:
                    # Check for stall
                    if time.time() - last_activity > 60:
                        raise TimeoutError("Response stream stalled")
                    
                    last_activity = time.time()
                    
                    try:
                        data = json.loads(line)
                        if "response" in data:
                            full_response.append(data["response"])
                        
                        # Check if done
                        if data.get("done", False):
                            break
                            
                    except json.JSONDecodeError:
                        continue
            
            return "".join(full_response).strip()
            
        except Exception as e:
            logger.error(f"Streaming failed: {e}")
            raise

    def _preprocess_logs(self, messages: List[str]) -> List[str]:
        """Preprocess logs to reduce size and improve quality."""
        # Remove duplicate consecutive messages
        deduplicated: List[str] = []
        last_msg = None
        dup_count = 0
        
        for msg in messages:
            if msg == last_msg:
                dup_count += 1
            else:
                if dup_count > 0 and deduplicated:
                    deduplicated[-1] += f" [repeated {dup_count} times]"
                deduplicated.append(msg)
                last_msg = msg
                dup_count = 0
        
        # Handle final duplicates
        if dup_count > 0 and deduplicated:
            deduplicated[-1] += f" [repeated {dup_count} times]"
        
        # Filter out low-value messages
        filtered = []
        skip_patterns = [
            r"^\s*$",  # Empty lines
            r"^-+$",   # Separator lines
            r"systemd\[\d+\]: Started Session",  # Noisy systemd messages
        ]
        
        for msg in deduplicated:
            if not any(re.match(pattern, msg) for pattern in skip_patterns):
                filtered.append(msg)
        
        logger.info(f"Preprocessed {len(messages)} -> {len(filtered)} messages")
        return filtered

    def get_cache_stats(self) -> Dict[str, Union[int, float]]:
        """Get cache statistics."""
        return self.cache.stats()


def get_summarizer(config: Dict[str, Any]) -> OllamaSummarizer:
    """Get or create global summarizer instance."""
    global _summarizer

    with _summarizer_lock:
        if _summarizer is None:
            _summarizer = OllamaSummarizer(
                host=config.get("ollama_host", DEFAULT_HOST),
                model=config.get("model", DEFAULT_MODEL),
                timeout=config.get("timeout", DEFAULT_TIMEOUT),
                cache_ttl=config.get("cache_ttl", CACHE_TTL),
            )

    return _summarizer


def summarize_log_chunk(
    lines: List[str], config: Dict[str, Any], use_cache: bool = True
) -> str:
    """
    Main entry point for log summarization.

    Args:
        lines: Log lines to summarize
        config: Configuration dictionary
        use_cache: Whether to use caching

    Returns:
        Summary string
    """
    if not lines:
        return "[No logs to summarize]"

    # Get configuration
    lines_per_prompt = config.get("lines_per_prompt", DEFAULT_LINES_PER_PROMPT)
    prompt_template = config.get("prompt")

    # Limit lines if needed
    if len(lines) > lines_per_prompt:
        logger.info(f"Limiting {len(lines)} lines to {lines_per_prompt}")
        lines = lines[-lines_per_prompt:]

    # Get summarizer
    summarizer = get_summarizer(config)

    # Summarize
    response = summarizer.summarize(
        lines=lines, prompt_template=prompt_template, use_cache=use_cache
    )

    # Log performance metrics
    if not response.cached:
        logger.info(
            f"Summary generated in {response.processing_time:.2f}s "
            f"for {response.line_count} lines using {response.model}"
        )

    return response.summary


def summarize_logs_with_ollama(
    prompt: str,
    model: str = DEFAULT_MODEL,
    host: str = DEFAULT_HOST,
    timeout: int = DEFAULT_TIMEOUT,
) -> str:
    """
    Legacy compatibility function.

    Args:
        prompt: Full prompt including logs
        model: Model name
        host: Ollama host
        timeout: Request timeout

    Returns:
        Summary string
    """
    try:
        response = requests.post(
            f"{host}/api/generate",
            json={"model": model, "prompt": prompt, "stream": False},
            timeout=timeout,
        )
        response.raise_for_status()
        return response.json().get("response", "").strip()
    except requests.RequestException as e:
        error_type = e.__class__.__name__
        logger.error(f"Error communicating with local LLM ({error_type}): {e}")
        return f"[Error: Could not generate summary due to {error_type}: {e}]"


def test_summarizer():
    """Test the summarizer with sample logs."""
    sample_logs = [
        "2024-01-15 10:23:45 ERROR Failed to connect to database: Connection refused",
        "2024-01-15 10:23:46 ERROR Retrying database connection...",
        "2024-01-15 10:23:47 WARNING High memory usage detected: 85%",
        "2024-01-15 10:23:48 INFO Service restarted successfully",
        "2024-01-15 10:23:49 ERROR Database connection failed after 3 retries",
    ]

    config = {
        "model": "mistral",
        "ollama_host": "http://localhost:11434",
        "timeout": 60,
        "lines_per_prompt": 50,
    }

    print("Testing summarizer...")
    summary = summarize_log_chunk(sample_logs, config)
    print(f"\nSummary:\n{summary}")

    # Get cache stats
    summarizer = get_summarizer(config)
    stats = summarizer.get_cache_stats()
    print(f"\nCache stats: {stats}")


if __name__ == "__main__":
    import sys

    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    if len(sys.argv) > 1 and sys.argv[1] == "test":
        test_summarizer()
    else:
        print("Usage: python summarizer.py test")