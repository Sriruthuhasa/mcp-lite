import unittest
from typing import Literal, Optional, Union

from mcp_lite import schema_from_function
from mcp_lite.schema import validate


def f(name: str, count: int = 3, ratio: float = 0.5, flag: bool = False,
      tags: Optional[list[str]] = None, mode: Literal["a", "b"] = "a",
      meta: dict = None, either: Union[int, str] = 1):
    """Do things.

    Args:
        name: The name.
        count (int): How many.
    """


class SchemaTests(unittest.TestCase):
    def setUp(self):
        self.s = schema_from_function(f)

    def test_types(self):
        p = self.s["properties"]
        self.assertEqual(p["name"]["type"], "string")
        self.assertEqual(p["count"]["type"], "integer")
        self.assertEqual(p["ratio"]["type"], "number")
        self.assertEqual(p["flag"]["type"], "boolean")
        self.assertEqual(p["tags"], {"type": "array", "items": {"type": "string"}, "default": None})
        self.assertEqual(p["mode"]["enum"], ["a", "b"])
        self.assertEqual(p["meta"]["type"], "object")
        self.assertEqual(len(p["either"]["anyOf"]), 2)

    def test_required_and_defaults(self):
        self.assertEqual(self.s["required"], ["name"])
        self.assertEqual(self.s["properties"]["count"]["default"], 3)

    def test_docstring_descriptions(self):
        self.assertEqual(self.s["properties"]["name"]["description"], "The name.")
        self.assertEqual(self.s["properties"]["count"]["description"], "How many.")

    def test_pep604_optional(self):
        def g(x: int | None):
            pass
        s = schema_from_function(g)
        self.assertEqual(s["properties"]["x"], {"type": "integer"})
        self.assertNotIn("required", s)

    def test_rejects_varargs_and_unknown_types(self):
        def h(*args):
            pass
        with self.assertRaises(TypeError):
            schema_from_function(h)

        def k(x: set):
            pass
        with self.assertRaises(TypeError):
            schema_from_function(k)

    def test_validate(self):
        self.assertEqual(validate({"name": "x"}, self.s), [])
        self.assertIn("missing required argument 'name'", validate({}, self.s))
        self.assertIn("unknown argument 'zzz'", validate({"name": "x", "zzz": 1}, self.s))
        self.assertTrue(validate({"name": "x", "count": True}, self.s))  # bool is not int
        self.assertTrue(validate({"name": "x", "count": 1.5}, self.s))
        self.assertEqual(validate({"name": "x", "ratio": 2}, self.s), [])  # int ok for number
        self.assertTrue(validate({"name": "x", "mode": "c"}, self.s))
        self.assertTrue(validate({"name": "x", "tags": ["a", 2]}, self.s))
        self.assertEqual(validate({"name": "x", "either": "s"}, self.s), [])
        self.assertTrue(validate({"name": "x", "either": 1.5}, self.s))
        self.assertEqual(validate([], self.s), ["arguments must be an object"])


if __name__ == "__main__":
    unittest.main()
