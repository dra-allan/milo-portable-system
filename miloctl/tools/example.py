"""
tools/example.py — Example tool demonstrating the tool framework.
================================================================

This is a simple example tool that shows how to implement a tool
for the Milo tool framework.
"""

from .base import Tool


class EchoTool(Tool):
    """A simple echo tool that repeats back the input message."""

    name = "echo"
    description = "Echo back a message with optional prefix and suffix."

    def run(self, message: str, prefix: str = "", suffix: str = "") -> str:
        """Echo a message with optional prefix and suffix.

        Args:
            message: The message to echo
            prefix: Optional prefix to add before the message
            suffix: Optional suffix to add after the message

        Returns:
            The formatted message
        """
        return f"{prefix}{message}{suffix}"


class TimerTool(Tool):
    """A tool that measures execution time of a command."""

    name = "timer"
    description = "Measure how long it takes to run a command."

    def run(self, command: str) -> dict:
        """Time the execution of a command.

        Args:
            command: The command to time (as a string)

        Returns:
            Dictionary with execution time and command output
        """
        import subprocess
        import time

        start_time = time.time()
        try:
            result = subprocess.run(
                command, shell=True, capture_output=True, text=True, timeout=30
            )
            elapsed = time.time() - start_time

            return {
                "command": command,
                "exit_code": result.returncode,
                "stdout": result.stdout,
                "stderr": result.stderr,
                "elapsed_seconds": round(elapsed, 3),
            }
        except subprocess.TimeoutExpired:
            elapsed = time.time() - start_time
            return {
                "command": command,
                "error": "Command timed out after 30 seconds",
                "elapsed_seconds": round(elapsed, 3),
            }
        except Exception as e:
            elapsed = time.time() - start_time
            return {
                "command": command,
                "error": str(e),
                "elapsed_seconds": round(elapsed, 3),
            }