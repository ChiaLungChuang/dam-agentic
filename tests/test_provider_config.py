"""Provider/model/credential wiring — keyless, deterministic.

These pin the two things that broke the first real Gemini run, verified against
direct curl diagnostics:

* **Model id.** `gemini-2.5-flash` returns 404 "no longer available to new
  users" on generateContent even though ListModels still lists it — the catalog
  is not what is callable. `gemini-3.6-flash` works.
* **Credential shape.** The key must reach the SDK as an *API key*
  (`google_api_key=` / `GOOGLE_API_KEY`), not as a credentials object. The same
  credential that authenticates via `x-goog-api-key` in curl returned 401
  ACCESS_TOKEN_TYPE_UNSUPPORTED when the SDK fell back to a Bearer-token path.

No network and no key: only the wiring is asserted.
"""

import pytest

pytest.importorskip("langchain_google_genai")

from agent.graph import DEFAULT_GOOGLE_MODEL, DEFAULT_MODELS, make_llm, resolved_model


def test_google_default_model_is_callable_one():
    """2.5-flash is 404 for new users; the default must be the model that works."""
    assert DEFAULT_GOOGLE_MODEL == "gemini-3.6-flash"
    assert DEFAULT_MODELS["google"] == DEFAULT_GOOGLE_MODEL
    assert resolved_model("google", None) == "gemini-3.6-flash"
    assert resolved_model("google", "gemini-3.6-pro") == "gemini-3.6-pro"   # overridable


def test_key_is_passed_as_api_key_not_bearer_credentials(monkeypatch):
    """The SDK must receive an API key. If it instead resolves ambient
    credentials, the request goes out as a Bearer token and Gemini rejects it
    with ACCESS_TOKEN_TYPE_UNSUPPORTED — the failure this pins."""
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key-not-real")
    llm = make_llm("google", None)
    key = llm.google_api_key
    assert key is not None
    # pydantic may wrap it in SecretStr; unwrap for comparison
    assert getattr(key, "get_secret_value", lambda: key)() == "test-key-not-real"
    assert llm.model.endswith(DEFAULT_GOOGLE_MODEL)
    assert getattr(llm, "credentials", None) is None       # never a credentials object


def test_missing_google_key_fails_with_an_actionable_message(monkeypatch, tmp_path):
    # Point .env loading at an empty dir so the developer's real key cannot leak
    # into this test and make it pass for the wrong reason.
    import agent.graph as g
    monkeypatch.setattr(g, "REPO", tmp_path)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)
    with pytest.raises(RuntimeError) as exc:
        make_llm("google", None)
    msg = str(exc.value)
    assert "GOOGLE_API_KEY" in msg and ".env" in msg
