from backend.verify import clean_verdict, is_split


def test_agree_and_disagree_is_a_split():
    verdicts = [{"verdict": "agree"}, {"verdict": "disagree"}, {"verdict": "unsure"}]
    assert is_split(verdicts)


def test_agree_and_unsure_is_not_a_split():
    verdicts = [{"verdict": "agree"}, {"verdict": "unsure"}]
    assert not is_split(verdicts)


def test_errors_never_cause_a_split():
    verdicts = [{"verdict": "agree"}, {"verdict": "error"}]
    assert not is_split(verdicts)


def test_odd_verdict_text_becomes_unsure():
    assert clean_verdict(" Agree ") == "agree"
    assert clean_verdict("probably") == "unsure"
