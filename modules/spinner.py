#!/usr/bin/env python3
"""
Enhanced Spinner Module - Terminal progress indicator
Production-ready with better error handling and features.
"""

import threading
import sys
import time
import os
from typing import Optional, List, Callable, Dict, TextIO, Any, Generator, Literal, Union
from contextlib import contextmanager
import signal
from types import FrameType


class Spinner:
    """
    Thread-safe terminal spinner with multiple styles and features.

    Example:
        # Basic usage
        spinner = Spinner("Processing")
        spinner.start()
        # ... do work ...
        spinner.stop()

        # Context manager usage
        with Spinner("Loading"):
            # ... do work ...
            pass
    """

    # Spinner styles
    STYLES: Dict[str, List[str]] = {
        "dots": ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"],
        "dots2": ["⣾", "⣽", "⣻", "⢿", "⡿", "⣟", "⣯", "⣷"],
        "dots3": ["⠋", "⠙", "⠚", "⠞", "⠖", "⠦", "⠴", "⠲", "⠳", "⠓"],
        "line": ["|", "/", "-", "\\"],
        "line2": ["⠂", "-", "–", "—", "–", "-"],
        "pipe": ["┤", "┘", "┴", "└", "├", "┌", "┬", "┐"],
        "simple": [".  ", ".. ", "...", "   "],
        "arrow": ["←", "↖", "↑", "↗", "→", "↘", "↓", "↙"],
        "pulse": ["▁", "▂", "▃", "▄", "▅", "▆", "▇", "█", "▇", "▆", "▅", "▄", "▃", "▁"],
        "bounce": ["⠁", "⠂", "⠄", "⠂"],
        "box": ["◰", "◳", "◲", "◱"],
        "circle": ["◐", "◓", "◑", "◒"],
        "square": ["◻", "◼"],
        "triangle": ["◢", "◣", "◤", "◥"],
        "classic": ["|", "/", "-", "\\"],  # Fallback for compatibility
    }

    def __init__(
        self,
        message: str = "Processing",
        style: str = "dots",
        speed: float = 0.1,
        color: Optional[str] = None,
        stream: Optional[TextIO] = None,
        disable: bool = False,
    ) -> None:
        """
        Initialize the spinner.

        Args:
            message: Message to display
            style: Spinner style (see STYLES)
            speed: Animation speed in seconds
            color: ANSI color code (e.g., '\033[32m' for green)
            stream: Output stream (default: sys.stdout)
            disable: Disable spinner (useful for non-TTY environments)
        """
        self.message = message
        self.speed = speed
        self.color = color
        self.stream: TextIO = stream or sys.stdout
        self.disable = disable

        # Set spinner frames
        self.frames = self.STYLES.get(style, self.STYLES["classic"])

        # Threading
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()

        # State
        self._running = False
        self._frame_index = 0

        # Check if we're in a TTY
        self._is_tty = hasattr(self.stream, "isatty") and self.stream.isatty()

        # Disable in non-TTY environments unless explicitly enabled
        if not self._is_tty and not os.environ.get("FORCE_SPINNER"):
            self.disable = True

        # Handle signals for cleanup
        self._original_sigint: Union[Callable[[int, Optional[FrameType]], Any], int, None]

    def __enter__(self) -> "Spinner":
        """Context manager entry."""
        self.start()
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> Literal[False]:
        """Context manager exit."""
        self.stop()
        return False

    def start(self) -> "Spinner":
        """Start the spinner."""
        if self.disable or self._running:
            return self

        with self._lock:
            if self._running:
                return self

            self._running = True
            self._stop_event.clear()

            if self._is_tty:
                self.stream.write("\033[s")  # Save cursor position
                self.stream.flush()

            self._original_sigint = signal.signal(signal.SIGINT, self._signal_handler)

            self._thread = threading.Thread(
                target=self._spin, name="SpinnerThread", daemon=True
            )
            self._thread.start()

        return self

    def stop(self, final_message: Optional[str] = None, symbol: str = "✓") -> None:
        """
        Stop the spinner.

        Args:
            final_message: Optional message to display after stopping
            symbol: Symbol to show with final message (default: ✓)
        """
        if self.disable or not self._running:
            return

        with self._lock:
            if not self._running:
                return

            self._running = False
            self._stop_event.set()

        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)

        if self._original_sigint is not None:
            signal.signal(signal.SIGINT, self._original_sigint)
            self._original_sigint = None

        if self._is_tty:
            self.stream.write("\033[u")  # Restore cursor position
            self.stream.write("\033[K")  # Clear to end of line

            if final_message:
                if self.color:
                    self.stream.write(f"{self.color}{symbol}\033[0m {final_message}\n")
                else:
                    self.stream.write(f"{symbol} {final_message}\n")

            self.stream.flush()
        elif final_message:
            self.stream.write(f"{symbol} {final_message}\n")
            self.stream.flush()

    def update(self, message: str) -> None:
        """Update the spinner message while running."""
        with self._lock:
            self.message = message

    def succeed(self, message: Optional[str] = None) -> None:
        """Stop spinner with success status."""
        final_msg = message or f"{self.message} done"
        self.stop(final_message=final_msg, symbol="✓")

    def fail(self, message: Optional[str] = None) -> None:
        """Stop spinner with failure status."""
        final_msg = message or f"{self.message} failed"
        old_color = self.color
        self.color = "\033[31m"
        self.stop(final_message=final_msg, symbol="✗")
        self.color = old_color

    def info(self, message: Optional[str] = None) -> None:
        """Stop spinner with info status."""
        final_msg = message or self.message
        self.stop(final_message=final_msg, symbol="ℹ")

    def warn(self, message: Optional[str] = None) -> None:
        """Stop spinner with warning status."""
        final_msg = message or self.message
        old_color = self.color
        self.color = "\033[33m"
        self.stop(final_message=final_msg, symbol="⚠")
        self.color = old_color

    def _spin(self) -> None:
        """Spin animation loop (runs in separate thread)."""
        try:
            while not self._stop_event.is_set():
                with self._lock:
                    frame = self.frames[self._frame_index % len(self.frames)]
                    message = self.message

                if self._is_tty:
                    output = f"\r\033[K{message}... "
                    output += f"{self.color}{frame}\033[0m" if self.color else frame
                    self.stream.write(output)
                    self.stream.flush()

                self._frame_index += 1
                self._stop_event.wait(self.speed)

        except Exception:
            pass

    def _signal_handler(self, signum: int, frame: Optional[FrameType]) -> None:
        """Handle signals for graceful cleanup."""
        self.stop()
        if callable(self._original_sigint):
            self._original_sigint(signum, frame)



class MultiSpinner:
    """
    Manage multiple spinners for parallel operations.
    """

    def __init__(self) -> None:
        """Initialize multi-spinner manager."""
        self.spinners: Dict[str, Spinner] = {}
        self._lock = threading.Lock()

    def add(self, name: str, message: str, **kwargs: Any) -> Spinner:
        """Add a new spinner."""
        with self._lock:
            spinner = Spinner(message, **kwargs)
            self.spinners[name] = spinner
            return spinner

    def start(self) -> None:
        """Start all spinners."""
        for spinner in self.spinners.values():
            spinner.start()

    def stop(self) -> None:
        """Stop all spinners."""
        for spinner in self.spinners.values():
            spinner.stop()

    def update(self, name: str, message: str) -> None:
        """Update a specific spinner."""
        if name in self.spinners:
            self.spinners[name].update(message)

    def succeed(self, name: str, message: Optional[str] = None) -> None:
        """Mark a spinner as succeeded."""
        if name in self.spinners:
            self.spinners[name].succeed(message)

    def fail(self, name: str, message: Optional[str] = None) -> None:
        """Mark a spinner as failed."""
        if name in self.spinners:
            self.spinners[name].fail(message)


@contextmanager
def spinner(message: str = "Processing", **kwargs: Any) -> Generator[Spinner, None, None]:
    """
    Convenience context manager for spinner.
    """
    s = Spinner(message, **kwargs)
    try:
        s.start()
        yield s
    finally:
        s.stop()


def demo() -> None:
    """Demonstrate spinner functionality."""
    import random

    print("Spinner Demo\n")

    print("1. Different spinner styles:")
    for style_name in ["dots", "line", "pulse", "bounce", "circle"]:
        with spinner(f"Testing {style_name} style", style=style_name) as sp:
            time.sleep(1.5)
            sp.succeed()

    print("\n2. Status indicators:")

    with spinner("Successful operation") as sp:
        time.sleep(1)
        sp.succeed()

    with spinner("Failed operation") as sp:
        time.sleep(1)
        sp.fail()

    with spinner("Warning operation") as sp:
        time.sleep(1)
        sp.warn("Operation completed with warnings")

    with spinner("Info operation") as sp:
        time.sleep(1)
        sp.info("Operation completed")

    print("\n3. Dynamic updates:")
    with spinner("Dynamic message") as sp:
        for i in range(5):
            sp.update(f"Processing item {i+1}/5")
            time.sleep(0.5)
        sp.succeed("All items processed")

    print("\n4. Multiple spinners:")
    multi = MultiSpinner()
    multi.add("download", "Downloading files", style="dots")
    multi.add("process", "Processing data", style="pulse")
    multi.add("upload", "Uploading results", style="bounce")

    multi.start()
    time.sleep(1)
    multi.succeed("download", "Files downloaded")
    time.sleep(1)
    multi.succeed("process", "Data processed")
    time.sleep(1)
    multi.succeed("upload", "Results uploaded")

    print("\nDemo complete!")


if __name__ == "__main__":
    demo()