from unittest.mock import Mock
from buzzer import BuzzerScheduler
import config

def test_mute_coalescing_and_exact_duty_budget(monkeypatch):
    command = Mock(return_value='OK')
    bz = BuzzerScheduler(clock_fn=lambda: 100, command=command)
    bz.trigger('fungus')
    bz.trigger('worm')
    assert bz._pending[0] == 'worm'
    bz.mute(True)
    assert bz._pending is None
    command.assert_called_with('BUZZ_OFF')
    assert bz._reserve('worm',100) is None
    bz.mute(False)
    monkeypatch.setattr(config,'BUZZ_MAX_ON_PER_MIN_S',.2)
    assert bz._reserve('worm',100) is not None
    assert bz._reserve('fungus',111) is None
    assert bz._reserve('worm',161) is not None
