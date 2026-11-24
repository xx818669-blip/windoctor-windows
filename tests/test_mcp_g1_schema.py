"""Frozen synthetic schema vectors against the production descriptor."""

import asyncio
import copy
import hashlib
import json
import unittest

from test_mcp_g1_support import ROOT, SENTINEL, VERSIONS, synthetic_snapshot
from jsonschema import Draft7Validator, Draft202012Validator
from mcp_types.methods import serialize_server_result
from pydantic import ValidationError
from windoctor_mcp import errors, schemas
from windoctor_mcp.handlers import Handlers


def pointer(document, path):
    for key in path.split("/")[1:]:
        key = key.replace("~1", "/").replace("~0", "~")
        document = document[int(key)] if isinstance(document, list) else document[key]
    return document


def materialize(directory, case):
    value = copy.deepcopy(case["inline"]) if "inline" in case else json.loads((directory / case["base"]).read_text(encoding="utf-8"))
    if "extract" in case:
        value = pointer(value, case["extract"])
    for change in case.get("mutations", []):
        parent_path, _, key = change["path"].rpartition("/")
        parent = pointer(value, parent_path)
        key = key.replace("~1", "/").replace("~0", "~")
        if isinstance(parent, list):
            key = int(key)
        if change["op"] == "remove":
            del parent[key]
        else:
            parent[key] = copy.deepcopy(change["value"])
    return value


class SchemaTests(unittest.TestCase):
    def test_descriptor_equals_frozen_and_roots_are_objects(self):
        expected = json.loads((ROOT / "docs/mcp/fixtures/capture.tool.json").read_text(encoding="utf-8"))
        self.assertEqual(schemas.tool_descriptor(), expected)
        for name in ("inputSchema", "outputSchema"):
            with self.subTest(schema=name):
                schema = expected[name]
                self.assertEqual(schema["type"], "object")
                Draft202012Validator.check_schema(schema)
        altered = schemas.tool_descriptor()
        altered["inputSchema"]["type"] = "array"
        self.assertEqual(schemas.input_schema()["type"], "object")

    def test_tools_list_serialization_by_protocol_version(self):
        result = asyncio.run(Handlers(scope=None).list_tools(ctx=None, params=None))
        data = result.model_dump(by_alias=True, mode="json", exclude_none=True)
        expected_tools = [schemas.tool_descriptor()]
        for version in VERSIONS:
            with self.subTest(version=version):
                wire = serialize_server_result("tools/list", version, data)
                self.assertEqual(wire["tools"], expected_tools)

            if version == "2026-07-28":
                continue
            for field in ("inputSchema", "outputSchema"):
                for change, error_type in (("missing", "missing"), ("array", "literal_error")):
                    with self.subTest(version=version, field=field, change=change):
                        invalid = copy.deepcopy(data)
                        schema = invalid["tools"][0][field]
                        if change == "missing":
                            del schema["type"]
                        else:
                            schema["type"] = "array"
                        with self.assertRaises(ValidationError) as caught:
                            serialize_server_result("tools/list", version, invalid)
                        issues = caught.exception.errors(include_input=False)
                        self.assertEqual(
                            [(issue["loc"], issue["type"]) for issue in issues],
                            [(("tools", 0, field, "type"), error_type)],
                        )

    def test_all_68_frozen_input_output_vectors(self):
        directory = ROOT / "docs/mcp"
        cases = json.loads((directory / "review-vectors.json").read_text(encoding="utf-8"))["schema_cases"]
        self.assertEqual(len(cases), 68)
        validators = {"fixtures/capture.input.schema.json": Draft202012Validator(schemas.input_schema()),
                      "fixtures/capture.output.schema.json": Draft202012Validator(schemas.output_schema())}
        for case in cases:
            with self.subTest(case=case["id"]):
                self.assertEqual(validators[case["schema"]].is_valid(materialize(directory, case)), case["expected_valid"])

    def test_core_projection_equivalence_23_snapshot_vectors(self):
        directory = ROOT / "docs/contracts"
        source = directory / "snapshot.schema.json"
        manifest = json.loads((ROOT / "docs/mcp/fixtures/projection-manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), manifest["source"]["sha256"])
        output = schemas.output_schema()
        pure = {"$schema": "https://json-schema.org/draft/2020-12/schema", "$defs": output["$defs"], "$ref": "#/$defs/coreSnapshot"}
        original, projected = Draft7Validator(json.loads(source.read_text())), Draft202012Validator(pure)
        cases = json.loads((directory / "boundary-vectors.json").read_text(encoding="utf-8"))["schema_cases"]
        applicable = [case for case in cases if case["schema"] == "snapshot.schema.json"]
        self.assertEqual(len(applicable), 23)
        self.assertEqual(len(cases) - len(applicable), 13)  # Non-snapshot cases are not projection tests.
        for case in applicable:
            with self.subTest(case=case["id"]):
                value = materialize(directory, case)
                self.assertEqual(original.is_valid(value), case["expected_valid"])
                self.assertEqual(projected.is_valid(value), original.is_valid(value))
        canonical = json.dumps(output["$defs"]["coreSnapshot"], ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        self.assertEqual(hashlib.sha256(canonical).hexdigest(), manifest["projection"]["canonical_sha256"])

    def test_finite_envelope_and_error_sanitizer(self):
        for modern in (False, True):
            for code in sorted(errors.ADAPTER_CODES):
                with self.subTest(modern=modern, code=code):
                    result = errors.adapter_error(code).model_dump(by_alias=True, exclude_none=True)
                    if modern:
                        result["_meta"] = errors.server_metadata()
                    else:
                        result.pop("resultType", None)
                    clean = errors.safe_tool_result(result)
                    self.assertEqual(clean["structuredContent"]["error"]["detail"]["code"], code)
                    self.assertEqual(json.loads(clean["content"][0]["text"]), clean["structuredContent"])
                    self.assertTrue(Draft202012Validator(schemas.output_schema()).is_valid(clean["structuredContent"]))
        envelope = {"ok": True, "snapshot": synthetic_snapshot()}
        result = errors.tool_result(envelope).model_dump(by_alias=True, exclude_none=True)
        result["_meta"] = errors.server_metadata()
        for mutation in ("meta", "text", "extra", "source", "flag"):
            with self.subTest(mutation=mutation):
                invalid = copy.deepcopy(result)
                if mutation == "meta":
                    invalid["_meta"]["private"] = SENTINEL
                elif mutation == "text":
                    invalid["content"][0]["text"] = SENTINEL
                elif mutation == "extra":
                    invalid["structuredContent"]["private"] = SENTINEL
                elif mutation == "source":
                    invalid["structuredContent"]["snapshot"]["source"]["declared_context"] = "terminal"
                else:
                    invalid["isError"] = True
                clean = errors.safe_tool_result(invalid)
                self.assertEqual(clean["structuredContent"]["error"]["detail"]["code"], "A_INTERNAL")
                self.assertNotIn(SENTINEL, json.dumps(clean))
