"""One local UTF-8 subtitle file; accepts only the inspected response channel."""

import os
import tempfile
from pathlib import Path

from virtual_ai.schemas import Response


class SubtitleError(Exception):
    pass


class SubtitleWriter:
    def __init__(self, settings):
        self.settings = settings
        self._path = Path(settings.path)

    def write(self, response: Response) -> None:
        if not self.settings.enabled:
            return
        if not isinstance(response, Response) or not isinstance(response.final, str):
            raise SubtitleError("Subtitles require an inspected Response.")
        self._replace(response.final)

    def clear(self) -> None:
        if self.settings.enabled:
            self._replace("")

    def _replace(self, text):
        temporary = None
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            fd, temporary = tempfile.mkstemp(
                dir=self._path.parent, prefix="subtitle-", suffix=".tmp"
            )
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
                stream.write(text)
            # Readers see the old complete answer or the new complete answer.
            os.replace(temporary, self._path)
        except (OSError, UnicodeError):
            raise SubtitleError("Subtitle file update failed.") from None
        finally:
            if temporary is not None:
                try:
                    Path(temporary).unlink(missing_ok=True)
                except OSError:
                    raise SubtitleError(
                        "Subtitle temporary file cleanup failed."
                    ) from None
