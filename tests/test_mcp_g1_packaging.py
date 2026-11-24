"""Optional SDK location and collection-free import checks in real processes."""

import json
from pathlib import Path
import subprocess
import sys
import unittest

from test_mcp_g1_support import ROOT, PYTHON, case_dir, child_env


class PackagingTests(unittest.TestCase):
    def test_sdk_import_is_from_approved_environment(self):
        script = """
import importlib.metadata as metadata
from pathlib import Path
import mcp, anyio, mcp_types, sys
site=Path(sys.prefix)/'Lib/site-packages'
assert all(Path(m.__file__).resolve().is_relative_to(site.resolve()) for m in (mcp,anyio,mcp_types))
assert metadata.version('mcp')=='2.3.0'
assert metadata.version('anyio')=='4.16.0'
print('APPROVED_IMPORT_OK')
"""
        directory = case_dir("sdk-import")
        result = subprocess.run([str(PYTHON), "-I", "-B", "-c", script], cwd=directory,
                                env=child_env(directory), capture_output=True, timeout=10)
        self.assertTrue(result.returncode == 0 and result.stdout == b"APPROVED_IMPORT_OK\r\n" and not result.stderr,
                        "SDK import check failed")
        self.assertFalse((ROOT / "mcp/__init__.py").exists())

    def test_default_core_and_entry_import_need_no_sdk_or_collection(self):
        script = """
import importlib.metadata as metadata, importlib.util, sys
from pathlib import Path
from unittest.mock import patch
root=Path(sys.argv[1]);sys.path.insert(0,str(root))
try:
    metadata.version('mcp')
except metadata.PackageNotFoundError:
    pass
else:
    raise AssertionError('Default interpreter unexpectedly has SDK')
import cli.capture, cli.shared.scope
with patch.object(cli.capture, 'capture', side_effect=AssertionError('Import collected data')), patch.object(cli.shared.scope, 'load_scope', side_effect=AssertionError('Import loaded scope')):
    import cli.__main__, diff.__main__
    spec=importlib.util.spec_from_file_location('windoctor_entry_check',root/'mcp/run_stdio.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
assert not any(name=='mcp' or name.startswith('mcp.') or name=='anyio' for name in sys.modules)
print('CORE_IMPORT_OK')
"""
        directory = case_dir("core-import")
        base_python = Path(sys.base_prefix) / "python.exe"
        result = subprocess.run([str(base_python), "-I", "-B", "-c", script, str(ROOT)], cwd=directory,
                                env=child_env(directory), capture_output=True, timeout=10)
        self.assertTrue(result.returncode == 0 and result.stdout == b"CORE_IMPORT_OK\r\n" and not result.stderr,
                        "Default core or entry import isolation failed")
