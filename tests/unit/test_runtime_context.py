from moonwing.services.runtime_context import build_runtime_context, running_in_container


def test_build_runtime_context_keys():
    ctx = build_runtime_context()
    assert "in_container" in ctx
    assert isinstance(ctx["in_container"], bool)
    assert ctx["in_container"] == running_in_container()
