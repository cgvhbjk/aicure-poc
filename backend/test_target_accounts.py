"""Known-customer / CRO matching must use word boundaries, not bare substrings,
so a distinctive fragment can't hide inside an unrelated org name (§6)."""
from target_accounts import is_known_customer, is_cro


def test_real_customer_matches():
    assert is_known_customer("Neumora Therapeutics, Inc.")
    assert is_known_customer("NEUMORA")
    assert is_known_customer("Bristol Myers Squibb Company")
    assert is_known_customer("F. Hoffmann-La Roche")          # resolves to roche


def test_substring_false_positives_rejected():
    # The exact collisions the substring matcher produced.
    assert not is_known_customer("University of Rochester")    # contains "roche"
    assert not is_known_customer("University of Bristol")      # contains "bristol"
    assert not is_known_customer("Silicon Therapeutics")       # contains "icon"


def test_cro_detection():
    assert is_cro("ICON plc")
    assert is_cro("IQVIA RDS Inc")
    assert not is_cro("Silicon Therapeutics")                  # not a CRO ("icon")
    assert not is_cro("")
