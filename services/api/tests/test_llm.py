from northstar_api.services.llm import _strip_hidden_reasoning


def test_hidden_reasoning_is_removed_from_visible_model_output() -> None:
    assert _strip_hidden_reasoning("<think>private chain</think>Final answer") == "Final answer"
    assert _strip_hidden_reasoning("Answer<reasoning>private chain</reasoning>") == "Answer"


def test_unterminated_hidden_reasoning_is_not_exposed() -> None:
    assert _strip_hidden_reasoning("Safe answer<think>unfinished private chain") == "Safe answer"
