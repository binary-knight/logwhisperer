#!/usr/bin/env python3
"""
Discord Alert Module - Send rich, formatted alerts to Discord webhooks
Production-ready version with enhanced features and reliability.
"""

import requests
import json
import time
import logging
from io import BytesIO
from typing import Optional, Dict, Any, List, Union, Tuple
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from urllib.parse import urlparse
import hashlib
import re

# Configure logging
logger = logging.getLogger(__name__)

# Constants
MAX_MESSAGE_LENGTH = 2000  # Discord's max message length
MAX_EMBED_LENGTH = 6000  # Discord's max total embed length
MAX_FIELD_LENGTH = 1024  # Discord's max field value length
MAX_FILE_SIZE = 8 * 1024 * 1024  # 8MB Discord file limit
MAX_EMBEDS = 10  # Discord's max embeds per message
RATE_LIMIT_RETRY_MAX = 3
DEFAULT_TIMEOUT = 30


class AlertLevel(Enum):
    """Alert severity levels with Discord color codes."""

    INFO = 0x3498DB  # Blue
    NOTICE = 0x5DADE2  # Light Blue
    WARNING = 0xF39C12  # Orange
    ERROR = 0xE74C3C  # Red
    CRITICAL = 0xC0392B  # Dark Red
    FATAL = 0x7B241C  # Very Dark Red

    @classmethod
    def from_string(cls, level: str) -> "AlertLevel":
        """Convert string to AlertLevel."""
        try:
            return cls[level.upper()]
        except KeyError:
            logger.warning(f"Unknown alert level: {level}, defaulting to INFO")
            return cls.INFO


@dataclass
class DiscordEmbed:
    """Discord embed structure."""

    title: Optional[str] = None
    description: Optional[str] = None
    color: Optional[int] = None
    fields: List[Dict[str, Any]] = field(default_factory=list)
    footer: Optional[Dict[str, str]] = None
    timestamp: Optional[str] = None
    author: Optional[Dict[str, str]] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to Discord API format."""
        embed: Dict[str, Any] = {}

        if self.title:
            embed["title"] = self._truncate(self.title, 256)

        if self.description:
            embed["description"] = self._truncate(self.description, 4096)

        if self.color is not None:
            embed["color"] = self.color

        if self.fields:
            embed["fields"] = [
                {
                    "name": self._truncate(f["name"], 256),
                    "value": self._truncate(f["value"], MAX_FIELD_LENGTH),
                    "inline": f.get("inline", False),
                }
                for f in self.fields[:25]  # Discord limit: 25 fields
            ]

        if self.footer:
            embed["footer"] = {
                "text": self._truncate(self.footer.get("text", ""), 2048),
            }
            if self.footer.get("icon_url"):
                embed["footer"]["icon_url"] = self.footer["icon_url"]

        if self.timestamp:
            embed["timestamp"] = self.timestamp

        if self.author:
            embed["author"] = {
                "name": self._truncate(self.author.get("name", ""), 256),
            }
            if self.author.get("url"):
                embed["author"]["url"] = self.author["url"]
            if self.author.get("icon_url"):
                embed["author"]["icon_url"] = self.author["icon_url"]

        return embed

    def _truncate(self, text: str, max_length: int) -> str:
        """Truncate text to max length."""
        if len(text) <= max_length:
            return text
        return text[: max_length - 3] + "..."

    def total_length(self) -> int:
        """Calculate total embed length for Discord limits."""
        length = 0
        if self.title:
            length += len(self.title)
        if self.description:
            length += len(self.description)
        if self.footer and "text" in self.footer:
            length += len(self.footer["text"])
        if self.author and "name" in self.author:
            length += len(self.author["name"])
        for field in self.fields:
            length += len(field.get("name", "")) + len(field.get("value", ""))
        return length


@dataclass
class AlertStats:
    """Statistics for alert formatting."""

    total_lines: int = 0
    by_level: Dict[str, int] = field(default_factory=dict)
    sources: List[str] = field(default_factory=list)
    time_range: Optional[Tuple[str, str]] = None

    def get_summary(self) -> str:
        """Get a summary string."""
        parts = [f"Total: {self.total_lines} entries"]

        # Add level breakdown if available
        if self.by_level:
            level_str = ", ".join([f"{k}: {v}" for k, v in self.by_level.items()])
            parts.append(f"Levels: {level_str}")

        # Add unique sources
        if self.sources:
            unique_sources = list(set(self.sources))[:5]  # Limit to 5
            parts.append(f"Sources: {', '.join(unique_sources)}")

        return " | ".join(parts)


class DiscordWebhook:
    """Enhanced Discord webhook client with production features."""

    def __init__(
        self,
        webhook_url: str,
        username: Optional[str] = "LogWhisperer",
        avatar_url: Optional[str] = None,
        timeout: int = DEFAULT_TIMEOUT,
    ):
        """Initialize Discord webhook client."""
        self.webhook_url = webhook_url
        self.username = username
        self.avatar_url = avatar_url
        self.timeout = timeout

        # Validate webhook URL
        self._validate_webhook_url()

        # Session for connection reuse
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": "LogWhisperer/1.0"})

    def _validate_webhook_url(self) -> None:
        """Validate Discord webhook URL format."""
        try:
            parsed = urlparse(self.webhook_url)
            if not all(
                [
                    parsed.scheme in ["http", "https"],
                    parsed.netloc in ["discord.com", "discordapp.com"],
                    "/api/webhooks/" in parsed.path,
                ]
            ):
                raise ValueError("Invalid Discord webhook URL format")
        except Exception as e:
            raise ValueError(f"Invalid webhook URL: {e}")

    def send(
        self,
        content: Optional[str] = None,
        embeds: Optional[List[DiscordEmbed]] = None,
        file: Optional[Tuple[str, bytes]] = None,
        allowed_mentions: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """
        Send a message to Discord.

        Args:
            content: Text content (max 2000 chars)
            embeds: List of embed objects (max 10)
            file: Tuple of (filename, file_content)
            allowed_mentions: Mention permissions

        Returns:
            True if successful, False otherwise
        """
        if not content and not embeds and not file:
            logger.warning("No content, embeds, or file to send")
            return False

        # Prepare payload
        payload: Dict[str, Any] = {}

        if self.username:
            payload["username"] = self.username

        if self.avatar_url:
            payload["avatar_url"] = self.avatar_url

        if content:
            payload["content"] = content[:MAX_MESSAGE_LENGTH]

        if embeds:
            payload["embeds"] = [e.to_dict() for e in embeds[:MAX_EMBEDS]]

        if allowed_mentions is None:
            # Default: don't ping @everyone or @here
            payload["allowed_mentions"] = {
                "parse": ["users", "roles"],
                "replied_user": False,
            }
        else:
            payload["allowed_mentions"] = allowed_mentions

        # Send request
        return self._send_request(payload, file)

    def _send_request(
        self, payload: Dict[str, Any], file: Optional[Tuple[str, bytes]] = None
    ) -> bool:
        """Send request with retry logic."""
        retry_count = 0

        while retry_count <= RATE_LIMIT_RETRY_MAX:
            try:
                if file:
                    # Send as multipart/form-data with file
                    filename, file_content = file
                    files = {"file": (filename, file_content)}
                    data = {"payload_json": json.dumps(payload)}
                    response = self._session.post(
                        self.webhook_url, data=data, files=files, timeout=self.timeout
                    )
                else:
                    # Send as JSON
                    response = self._session.post(
                        self.webhook_url, json=payload, timeout=self.timeout
                    )

                if response.status_code == 204:
                    logger.debug("Alert sent successfully")
                    return True

                elif response.status_code == 429:
                    # Rate limited
                    retry_after = response.json().get("retry_after", 1)
                    logger.warning(f"Rate limited, retrying after {retry_after}s")
                    time.sleep(retry_after)
                    retry_count += 1
                    continue

                else:
                    logger.error(
                        f"Discord API error: {response.status_code} - {response.text}"
                    )
                    return False

            except requests.exceptions.Timeout:
                logger.error(f"Request timed out after {self.timeout}s")
                return False

            except requests.exceptions.RequestException as e:
                logger.error(f"Network error: {e}")
                return False

            except Exception as e:
                logger.error(f"Unexpected error: {e}")
                return False

        logger.error("Max retries exceeded")
        return False

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self._session.close()


class AlertFormatter:
    """Format alerts for Discord with intelligent splitting."""

    def __init__(self, max_message_length: int = MAX_MESSAGE_LENGTH):
        self.max_message_length = max_message_length

    def format_simple(self, message: str, level: str = "INFO") -> Dict[str, Any]:
        """Format a simple text alert."""
        alert_level = AlertLevel.from_string(level)

        # If message is short enough, send as content
        if len(message) <= self.max_message_length:
            return {"content": f"**[{level}] LogWhisperer Alert:**\n{message}"}

        # Otherwise, create embed with truncation
        embed = DiscordEmbed(
            title=f"[{level}] LogWhisperer Alert",
            description=message[:4096],  # Discord's embed description limit
            color=alert_level.value,
            timestamp=datetime.utcnow().isoformat(),
            footer={"text": "LogWhisperer Alert System"},
        )

        return {"embeds": [embed]}

    def format_rich(
        self,
        message: str,
        level: str = "INFO",
        title: Optional[str] = None,
        stats: Optional[AlertStats] = None,
        source: Optional[str] = None,
        summary: Optional[str] = None,
        escalated_summary: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Format a rich alert with embeds."""
        alert_level = AlertLevel.from_string(level)

        # Create main embed
        embed = DiscordEmbed(
            title=title or f"[{level}] Log Alert",
            color=alert_level.value,
            timestamp=datetime.utcnow().isoformat(),
            footer={"text": "LogWhisperer Alert System"},
        )

        # Add source field
        if source:
            embed.fields.append(
                {"name": "📄 Source", "value": f"`{source}`", "inline": True}
            )

        # Add stats field
        if stats:
            embed.fields.append(
                {"name": "📊 Statistics", "value": stats.get_summary(), "inline": True}
            )

        # Add alert level field
        level_emoji = self._get_level_emoji(alert_level)
        embed.fields.append(
            {"name": "⚠️ Level", "value": f"{level_emoji} {level}", "inline": True}
        )

        # Add summaries
        if summary:
            # Truncate if needed
            if len(summary) > MAX_FIELD_LENGTH:
                summary = summary[: MAX_FIELD_LENGTH - 3] + "..."
            embed.fields.append(
                {"name": "🧠 Summary", "value": summary, "inline": False}
            )

        if escalated_summary:
            # Truncate if needed
            if len(escalated_summary) > MAX_FIELD_LENGTH:
                escalated_summary = escalated_summary[: MAX_FIELD_LENGTH - 3] + "..."
            embed.fields.append(
                {
                    "name": "🚨 Critical Findings",
                    "value": escalated_summary,
                    "inline": False,
                }
            )

        # Check total embed length
        if embed.total_length() > MAX_EMBED_LENGTH:
            # Need to truncate or split
            logger.warning("Embed too long, truncating fields")
            self._truncate_embed_fields(embed)

        return {"embeds": [embed]}

    def format_with_file(
        self, message: str, level: str = "INFO", filename: str = "log_alert.txt"
    ) -> Dict[str, Any]:
        """Format alert as file attachment."""
        alert_level = AlertLevel.from_string(level)

        # Create a simple embed for context
        embed = DiscordEmbed(
            title=f"[{level}] Log Alert - Full Details Attached",
            description="The complete log analysis is attached as a file.",
            color=alert_level.value,
            timestamp=datetime.utcnow().isoformat(),
            footer={"text": "LogWhisperer Alert System"},
        )

        # Add file info
        file_size = len(message.encode("utf-8"))
        embed.fields.append(
            {
                "name": "📎 Attachment",
                "value": f"Filename: `{filename}`\nSize: {self._format_size(file_size)}",
                "inline": True,
            }
        )

        return {"embeds": [embed], "file": (filename, message.encode("utf-8"))}

    def _get_level_emoji(self, level: AlertLevel) -> str:
        """Get emoji for alert level."""
        emoji_map = {
            AlertLevel.FATAL: "💀",
            AlertLevel.CRITICAL: "🔴",
            AlertLevel.ERROR: "❌",
            AlertLevel.WARNING: "⚠️",
            AlertLevel.NOTICE: "ℹ️",
            AlertLevel.INFO: "📝",
        }
        return emoji_map.get(level, "•")

    def _format_size(self, size_bytes: float) -> str:
        """Format byte size as human readable."""
        for unit in ["B", "KB", "MB"]:
            if size_bytes < 1024.0:
                return f"{size_bytes:.1f} {unit}"
            size_bytes /= 1024.0
        return f"{size_bytes:.1f} GB"

    def _truncate_embed_fields(self, embed: DiscordEmbed) -> None:
        """Truncate embed fields to fit within Discord limits."""
        # Calculate current total
        total = embed.total_length()

        # Truncate fields starting from the end
        for i in reversed(range(len(embed.fields))):
            if total <= MAX_EMBED_LENGTH:
                break

            field = embed.fields[i]
            current_length = len(field.get("name", "")) + len(field.get("value", ""))

            # Truncate value
            max_value_length = min(
                MAX_FIELD_LENGTH,
                MAX_EMBED_LENGTH - total + current_length - len(field.get("name", "")),
            )
            if max_value_length > 100:  # Keep at least 100 chars
                field["value"] = field["value"][: max_value_length - 3] + "..."
                total = embed.total_length()


def _build_mention_string(
    mentions: Optional[Union[List[str], Dict[str, List[str]]]], level: str
) -> str:
    """
    Build Discord mention string based on configuration.

    Args:
        mentions: User/role IDs to mention
        level: Current alert level

    Returns:
        Formatted mention string
    """
    if not mentions:
        return ""

    mention_ids: List[str] = []

    if isinstance(mentions, dict):
        # Level-based mentions
        # Check exact level match first
        if level in mentions:
            mention_ids.extend(mentions[level])

        # Also check severity hierarchy (e.g., CRITICAL alerts also notify ERROR watchers)
        level_hierarchy: Dict[str, List[str]] = {
            "FATAL": ["FATAL", "CRITICAL", "ERROR", "WARNING"],
            "CRITICAL": ["CRITICAL", "ERROR", "WARNING"],
            "ERROR": ["ERROR", "WARNING"],
            "WARNING": ["WARNING"],
            "NOTICE": ["NOTICE"],
            "INFO": ["INFO"],
        }

        for check_level in level_hierarchy.get(level, [level]):
            if check_level in mentions and check_level != level:
                mention_ids.extend(mentions[check_level])

    elif isinstance(mentions, list):
        # Simple list of IDs - mention for all alerts
        mention_ids = mentions

    # Remove duplicates while preserving order
    seen = set()
    unique_ids = []
    for id_str in mention_ids:
        if id_str and id_str not in seen:
            seen.add(id_str)
            unique_ids.append(id_str)

    if not unique_ids:
        return ""

    # Build mention string
    mention_parts = []
    for id_str in unique_ids:
        # Determine if it's a user or role ID
        if id_str.startswith("&"):
            # Role ID (already has & prefix)
            mention_parts.append(f"<@{id_str}>")
        elif id_str.startswith("<@") and id_str.endswith(">"):
            # Already formatted mention
            mention_parts.append(id_str)
        elif id_str.isdigit():
            # User ID
            mention_parts.append(f"<@{id_str}>")
        else:
            logger.warning(f"Invalid mention ID format: {id_str}")

    return " ".join(mention_parts) if mention_parts else ""


def send_alert(
    message: str,
    webhook_url: str,
    level: str = "INFO",
    username: Optional[str] = None,
    avatar_url: Optional[str] = None,
    use_embed: bool = True,
    stats: Optional[Dict[str, Any]] = None,
    source: Optional[str] = None,
    title: Optional[str] = None,
    mentions: Optional[Union[List[str], Dict[str, List[str]]]] = None,
) -> bool:
    """
    Send an alert to Discord with automatic formatting.

    Args:
        message: Alert message content
        webhook_url: Discord webhook URL
        level: Alert level (INFO, WARNING, ERROR, etc.)
        username: Override webhook username
        avatar_url: Override webhook avatar
        use_embed: Use rich embed formatting
        stats: Optional statistics dictionary
        source: Optional source identifier
        title: Optional custom title
        mentions: User/role IDs to mention. Can be:
                 - List of user/role IDs: ["123456789", "987654321"]
                 - Dict with level-based mentions: {"ERROR": ["123"], "CRITICAL": ["123", "456"]}

    Returns:
        True if sent successfully, False otherwise
    """
    if not webhook_url:
        logger.error("No webhook URL provided")
        return False

    if not message or not message.strip():
        logger.warning("Empty alert message")
        return False

    try:
        # Create webhook client
        with DiscordWebhook(
            webhook_url, username=username or "LogWhisperer", avatar_url=avatar_url
        ) as webhook:

            formatter = AlertFormatter()

            # Build mention string
            mention_str = _build_mention_string(mentions, level)

            # Determine formatting strategy
            message_size = len(message.encode("utf-8"))

            if message_size > MAX_FILE_SIZE:
                logger.error(
                    f"Message too large ({message_size} bytes), max is {MAX_FILE_SIZE}"
                )
                # Truncate and send what we can
                truncated_size = MAX_FILE_SIZE - 1000  # Leave some buffer
                message = message.encode("utf-8")[:truncated_size].decode(
                    "utf-8", errors="ignore"
                )
                message += "\n\n[Message truncated due to size limits]"

            formatted: Dict[str, Any]
            if not use_embed or message_size > 5000:
                # Use file attachment for large messages
                formatted = formatter.format_with_file(
                    message,
                    level,
                    f"logwhisperer_alert_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt",
                )
            elif stats or source or title:
                # Use rich formatting
                alert_stats = None
                if stats:
                    alert_stats = AlertStats(**stats)

                formatted = formatter.format_rich(
                    message, level, title=title, stats=alert_stats, source=source
                )
            else:
                # Use simple formatting
                formatted = formatter.format_simple(message, level)

            # Add mentions to content
            if mention_str:
                if "content" in formatted:
                    formatted["content"] = f"{mention_str}\n{formatted['content']}"
                else:
                    formatted["content"] = mention_str

            # Extract file if present
            file = formatted.pop("file", None)

            # Send alert
            return webhook.send(
                content=formatted.get("content"),
                embeds=[DiscordEmbed(**e) if isinstance(e, dict) else e for e in formatted.get("embeds", [])],
                file=file,
            )

    except ValueError as e:
        logger.error(f"Configuration error: {e}")
        return False

    except Exception as e:
        logger.exception(f"Unexpected error sending alert: {e}")
        return False


def send_alert_batch(
    messages: List[str],
    webhook_url: str,
    level: str = "INFO",
    batch_title: str = "Log Alert Batch",
) -> bool:
    """
    Send multiple alerts as a batch.

    Args:
        messages: List of alert messages
        webhook_url: Discord webhook URL
        level: Alert level for all messages
        batch_title: Title for the batch

    Returns:
        True if sent successfully, False otherwise
    """
    if not messages:
        logger.warning("No messages to send")
        return False

    # Combine messages
    combined = f"**{batch_title}**\n\n"
    combined += f"**Total Alerts:** {len(messages)}\n"
    combined += "=" * 40 + "\n\n"

    for i, msg in enumerate(messages, 1):
        combined += f"**Alert {i}:**\n{msg}\n\n"

        # Check size limit
        if len(combined.encode("utf-8")) > MAX_FILE_SIZE - 1000:
            combined += f"\n[{len(messages) - i} additional alerts truncated]"
            break

    return send_alert(
        combined,
        webhook_url,
        level=level,
        title=batch_title,
        use_embed=len(combined) < 4000,  # Use embed for smaller batches
    )


def send_alert_with_config(
    message: str,
    webhook_url: str,
    level: str = "INFO",
    config: Optional[Dict[str, Any]] = None,
    **kwargs: Any,
) -> bool:
    """
    Send alert with configuration-based mentions.

    This is a convenience wrapper that reads mention configuration
    from the LogWhisperer config and applies it automatically.

    Args:
        message: Alert message
        webhook_url: Discord webhook URL
        level: Alert level
        config: LogWhisperer configuration dict
        **kwargs: Additional arguments for send_alert

    Returns:
        True if sent successfully
    """
    mentions = None

    if config:
        monitor_config = config.get("monitor", {})
        mentions = monitor_config.get("discord_mentions")

    return send_alert(
        message=message,
        webhook_url=webhook_url,
        level=level,
        mentions=mentions,
        **kwargs,
    )


def test_webhook(webhook_url: str) -> bool:
    """
    Test if a webhook URL is valid and accessible.

    Args:
        webhook_url: Discord webhook URL to test

    Returns:
        True if webhook is valid and accessible
    """
    try:
        test_message = (
            "🧪 **LogWhisperer Test Message**\n\n"
            "This is a test message to verify your Discord webhook is configured correctly.\n"
            f"Timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )

        return send_alert(test_message, webhook_url, level="INFO", title="Webhook Test")

    except Exception as e:
        logger.error(f"Webhook test failed: {e}")
        return False


def main():
    """Main function for testing."""
    import argparse

    parser = argparse.ArgumentParser(description="Discord Alert Testing")
    parser.add_argument("webhook_url", help="Discord webhook URL")
    parser.add_argument(
        "--message", default="Test alert message", help="Message to send"
    )
    parser.add_argument("--level", default="INFO", help="Alert level")
    parser.add_argument("--test", action="store_true", help="Run webhook test")

    args = parser.parse_args()

    # Set up logging
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    if args.test:
        success = test_webhook(args.webhook_url)
        print(f"Webhook test: {'✓ Success' if success else '✗ Failed'}")
    else:
        success = send_alert(args.message, args.webhook_url, level=args.level)
        print(f"Alert sent: {'✓ Success' if success else '✗ Failed'}")


if __name__ == "__main__":
    main()