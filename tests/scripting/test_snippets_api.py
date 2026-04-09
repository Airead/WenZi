"""Tests for the Snippets API."""

from __future__ import annotations

from wenzi.scripting.api.snippets import SnippetsAPI


class _FakeStore:
    """Minimal stub that mimics SnippetStore for testing."""

    def __init__(self) -> None:
        self._snippets: list[dict[str, str]] = []

    @property
    def snippets(self) -> list[dict[str, str]]:
        return list(self._snippets)

    def find_by_keyword(self, keyword: str) -> dict[str, str] | None:
        for s in self._snippets:
            if s.get("keyword") == keyword:
                return s
        return None

    def add(
        self, name: str, keyword: str, content: str, category: str = "",
        auto_expand: bool = True, *, random: bool = False,
        variants: list[str] | None = None,
    ) -> bool:
        if self.find_by_keyword(keyword) is not None:
            return False
        d: dict = {
            "name": name, "keyword": keyword, "content": content,
            "category": category, "auto_expand": auto_expand,
        }
        if random:
            d["random"] = True
            d["variants"] = variants or [content]
        self._snippets.append(d)
        return True

    def remove(self, name: str, category: str = "") -> bool:
        for i, s in enumerate(self._snippets):
            if s["name"] == name and s.get("category", "") == category:
                self._snippets.pop(i)
                return True
        return False

    def update(
        self,
        name: str,
        category: str = "",
        *,
        new_name: str | None = None,
        new_keyword: str | None = None,
        content: str | None = None,
        new_category: str | None = None,
        new_auto_expand: bool | None = None,
        new_random: bool | None = None,
        new_variants: list[str] | None = None,
    ) -> bool:
        for s in self._snippets:
            if s["name"] == name and s.get("category", "") == category:
                if new_name is not None:
                    s["name"] = new_name
                if new_keyword is not None:
                    s["keyword"] = new_keyword
                if content is not None:
                    s["content"] = content
                if new_category is not None:
                    s["category"] = new_category
                if new_auto_expand is not None:
                    s["auto_expand"] = new_auto_expand
                if new_random is not None:
                    if new_random:
                        s["random"] = True
                    else:
                        s.pop("random", None)
                        s.pop("variants", None)
                if new_variants is not None:
                    s["variants"] = new_variants
                return True
        return False


class TestSnippetsAPI:
    def _api_with_store(self) -> tuple:
        api = SnippetsAPI()
        store = _FakeStore()
        api._set_store(store)
        return api, store

    def test_list_no_store(self):
        api = SnippetsAPI()
        assert api.list() == []

    def test_list_returns_snippets(self):
        api, store = self._api_with_store()
        store.add(name="Greeting", keyword="hi", content="Hello!")
        result = api.list()
        assert len(result) == 1
        assert result[0]["keyword"] == "hi"

    def test_get_found(self):
        api, store = self._api_with_store()
        store.add(name="Sig", keyword="sig", content="Best regards")
        assert api.get("sig") is not None
        assert api.get("sig")["content"] == "Best regards"

    def test_get_not_found(self):
        api, _ = self._api_with_store()
        assert api.get("nope") is None

    def test_get_no_store(self):
        api = SnippetsAPI()
        assert api.get("anything") is None

    def test_add_success(self):
        api, store = self._api_with_store()
        assert api.add(name="Test", keyword="tst", content="content") is True
        assert len(store.snippets) == 1

    def test_add_duplicate_keyword(self):
        api, store = self._api_with_store()
        store.add(name="A", keyword="dup", content="a")
        assert api.add(name="B", keyword="dup", content="b") is False

    def test_add_no_store(self):
        api = SnippetsAPI()
        assert api.add(name="X", keyword="x", content="x") is False

    def test_remove_success(self):
        api, store = self._api_with_store()
        store.add(name="Rm", keyword="rm", content="remove me")
        assert api.remove("rm") is True
        assert len(store.snippets) == 0

    def test_remove_not_found(self):
        api, _ = self._api_with_store()
        assert api.remove("nope") is False

    def test_remove_no_store(self):
        api = SnippetsAPI()
        assert api.remove("x") is False

    def test_update_success(self):
        api, store = self._api_with_store()
        store.add(name="Old", keyword="upd", content="old content")
        assert api.update("upd", content="new content") is True
        assert store.find_by_keyword("upd")["content"] == "new content"

    def test_update_not_found(self):
        api, _ = self._api_with_store()
        assert api.update("nope", content="x") is False

    def test_update_no_store(self):
        api = SnippetsAPI()
        assert api.update("x", content="y") is False

    def test_add_with_auto_expand(self):
        api, store = self._api_with_store()
        assert api.add(name="T", keyword="t", content="c", auto_expand=False) is True
        assert store.snippets[0]["auto_expand"] is False

    def test_add_default_auto_expand(self):
        api, store = self._api_with_store()
        assert api.add(name="T", keyword="t", content="c") is True
        assert store.snippets[0]["auto_expand"] is True

    def test_update_auto_expand(self):
        api, store = self._api_with_store()
        store.add(name="T", keyword="t", content="c")
        assert api.update("t", new_auto_expand=False) is True
        assert store.find_by_keyword("t")["auto_expand"] is False

    def test_add_random_snippet(self):
        api, store = self._api_with_store()
        variants = ["Thanks!", "Thank you!"]
        assert api.add(
            name="Thx", keyword="thx", content="Thanks!",
            random=True, variants=variants,
        ) is True
        s = store.snippets[0]
        assert s["random"] is True
        assert s["variants"] == variants

    def test_update_random(self):
        api, store = self._api_with_store()
        store.add(name="T", keyword="t", content="c", random=True, variants=["A", "B"])
        assert api.update("t", new_random=False) is True
        s = store.find_by_keyword("t")
        assert "random" not in s

    def test_update_variants(self):
        api, store = self._api_with_store()
        store.add(name="T", keyword="t", content="c", random=True, variants=["A"])
        assert api.update("t", new_variants=["X", "Y"]) is True
        s = store.find_by_keyword("t")
        assert s["variants"] == ["X", "Y"]

    def test_set_store_none(self):
        api, _ = self._api_with_store()
        api._set_store(None)
        assert api.list() == []
        assert api.get("anything") is None
