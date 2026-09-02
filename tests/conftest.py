from __future__ import annotations

from datetime import date

import pytest

from jarvis import Jarvis
from jarvis.integrations.calendar import MockCalendarProvider


@pytest.fixture
def jarvis() -> Jarvis:
    return Jarvis(calendar=MockCalendarProvider(today=date.today()))

